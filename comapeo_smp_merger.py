# -*- coding: utf-8 -*-
"""Merge multiple SMP archives into a single combined SMP file.

This module performs pure ZIP-level manipulation; no QGIS rendering is
needed. It parses each input SMP's ``style.json``, detects sources that
can be merged (same bounds + tile format, adjacent/non-overlapping zoom
ranges), re-indexes source folders, merges sources/layers/metadata, and
writes a combined ZIP archive.
"""

import hashlib
import json
import os
import re
import zipfile


# ---------------------------------------------------------------------------
# SMP tile URI pattern: smp://maps.v1/s/{sourceIndex}/{z}/{x}/{y}.{ext}
# Source indices are decimal folder names, matching comapeo_smp_generator.py.
# ---------------------------------------------------------------------------
_SOURCE_ID_RE = r'\d+'
_SMP_URI_RE = re.compile(
    r'^smp://maps\.v1/s/(' + _SOURCE_ID_RE
    + r')/\{z\}/\{x\}/\{y\}\.(\w+)$'
)

# Valid tile path pattern: s/{sourceIndex}/{digits}/{digits}/{digits}.{ext}
_TILE_PATH_RE = re.compile(
    r'^s/' + _SOURCE_ID_RE + r'/\d+/\d+/\d+\.\w+$'
)

# Floating-point tolerance for bounds comparison (degrees)
_BOUNDS_TOLERANCE = 1e-6


def _encode_source_index(source_index):
    """Encode a non-negative integer source index as a decimal string.

    :param source_index: Non-negative integer
    :returns: Decimal string
    """
    if source_index < 0:
        raise ValueError("Source index must be non-negative.")
    return str(source_index)


def _decode_source_index(encoded):
    """Decode a decimal source index string to an integer.

    :param encoded: Decimal source index string
    :returns: Integer source index
    """
    return int(encoded, 10)


def _parse_smp_uri(uri):
    """Return (source_index, extension) from an SMP tile URI string.

    :param uri: Tile URI like ``smp://maps.v1/s/0/{z}/{x}/{y}.png``
    :returns: Tuple (source_index_int, extension_str)
    :raises ValueError: If the URI does not match the SMP format
    """
    match = _SMP_URI_RE.match(uri)
    if not match:
        raise ValueError("Invalid SMP tile URI: {}".format(uri))
    return _decode_source_index(match.group(1)), match.group(2)


def _make_smp_uri(source_index, ext):
    """Build an SMP tile URI for a given source index and tile extension.

    :param source_index: Integer source folder index
    :param ext: Tile file extension (e.g. ``'png'``, ``'jpg'``)
    :returns: URI string
    """
    source_code = _encode_source_index(source_index)
    return "smp://maps.v1/s/{}/{{z}}/{{x}}/{{y}}.{}".format(
        source_code, ext
    )


def _is_safe_tile_path(arcname):
    """Return True if *arcname* is a safe, canonical tile path.

    Rejects paths containing ``..``, absolute paths, or any component
    that is not a simple ``s/{idx}/{z}/{x}/{y}.{ext}`` pattern.

    :param arcname: ZIP entry path to validate
    :returns: True if safe, False otherwise
    """
    if '\\' in arcname:
        return False
    if '..' in arcname.split('/'):
        return False
    if not _TILE_PATH_RE.match(arcname):
        return False
    return True


def _bounds_match(a, b, tolerance=_BOUNDS_TOLERANCE):
    """Return True if two [west, south, east, north] bounds are equal
    within *tolerance*.

    :param a: First bounds list
    :param b: Second bounds list
    :param tolerance: Maximum absolute difference per coordinate
    :returns: True if bounds match within tolerance
    """
    if a is None or b is None:
        return a is None and b is None
    return all(abs(a[i] - b[i]) <= tolerance for i in range(4))


def _union_bounds(bounds_list):
    """Compute the union of multiple [west, south, east, north] bounds.

    :param bounds_list: Iterable of 4-element bounds lists
    :returns: Union bounds as [west, south, east, north]
    """
    if not bounds_list:
        return [-180, -85.0511, 180, 85.0511]
    west = min(b[0] for b in bounds_list)
    south = min(b[1] for b in bounds_list)
    east = max(b[2] for b in bounds_list)
    north = max(b[3] for b in bounds_list)
    return [west, south, east, north]


def _is_valid_bounds(bounds):
    """Return True if *bounds* is a numeric four-element bbox list."""
    if not isinstance(bounds, list) or len(bounds) != 4:
        return False
    return all(isinstance(value, (int, float)) for value in bounds)


def _style_export_bounds(style):
    """Return the user-facing export bounds for an input style.

    Generated SMPs store their intended viewport in ``metadata.smp:bounds``.
    Prefer that value so merging World/Region/Local packages keeps the Local
    extent as the merged viewport. If older or hand-built SMPs omit metadata
    bounds, fall back to the union of source bounds.
    """
    metadata = style.get('metadata', {})
    meta_bounds = metadata.get('smp:bounds')
    if _is_valid_bounds(meta_bounds):
        return meta_bounds

    source_bounds = [
        source_def.get('bounds')
        for source_def in style.get('sources', {}).values()
        if _is_valid_bounds(source_def.get('bounds'))
    ]
    if source_bounds:
        return _union_bounds(source_bounds)
    return None


def _dedom(source_id, used_ids):
    """Return ``source_id`` with a numeric suffix if it already exists in
    ``used_ids``, updating ``used_ids`` in-place.

    :param source_id: Preferred source ID string
    :param used_ids: Set of already-assigned source IDs (mutated)
    :returns: Unique source ID string
    """
    if source_id not in used_ids:
        used_ids.add(source_id)
        return source_id
    suffix = 2
    while "{}_{}".format(source_id, suffix) in used_ids:
        suffix += 1
    deduped = "{}_{}".format(source_id, suffix)
    used_ids.add(deduped)
    return deduped


def _read_smp_style(smp_path):
    """Read and parse ``style.json`` from an SMP archive.

    :param smp_path: Path to the ``.smp`` file
    :returns: Parsed style dict
    :raises ValueError: If style.json is missing or archive is corrupt
    """
    try:
        with zipfile.ZipFile(smp_path, 'r') as zf:
            try:
                with zf.open('style.json') as fh:
                    return json.loads(fh.read())
            except KeyError:
                raise ValueError(
                    "SMP archive missing style.json: {}".format(smp_path)
                )
    except zipfile.BadZipFile:
        raise ValueError(
            "File is not a valid ZIP/SMP archive: {}".format(smp_path)
        )


def _find_mergeable_sources(candidates):
    """Identify groups of sources that can be merged into single sources.

    Two sources can be merged when they share the same tile extension,
    have matching bounds (within tolerance), and have non-overlapping or
    adjacent zoom ranges. Sources from the same input file are never
    merged with each other.

    :param candidates: List of dicts with keys:
        ``input_idx``, ``source_id``, ``source_def``, ``ext``,
        ``bounds``, ``minzoom``, ``maxzoom``
    :returns: List of groups (each group is a list of candidate indices)
    """
    groups = []
    assigned = set()

    for i, a in enumerate(candidates):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        for j in range(i + 1, len(candidates)):
            if j in assigned:
                continue
            b = candidates[j]
            # Never merge sources from the same input file
            if a['input_idx'] == b['input_idx']:
                continue
            # Must have same tile extension
            if a['ext'] != b['ext']:
                continue
            # Must have matching bounds
            if not _bounds_match(a['bounds'], b['bounds']):
                continue
            # Zoom ranges must not overlap
            if a['minzoom'] <= b['maxzoom'] \
                    and b['minzoom'] <= a['maxzoom']:
                continue
            # Check if zoom range is compatible with all current group
            # members (non-overlapping)
            compatible = True
            for k in group:
                g = candidates[k]
                if g['minzoom'] <= b['maxzoom'] \
                        and b['minzoom'] <= g['maxzoom']:
                    compatible = False
                    break
            if compatible:
                group.append(j)
                assigned.add(j)
        groups.append(group)

    return groups


def merge_smp_files(input_paths, output_path, feedback=None):
    """Merge two or more SMP archives into a single combined SMP file.

    Sources from different inputs that share the same tile format and
    geographic bounds with non-overlapping zoom ranges are automatically
    merged into a single source. For example, a source covering z12-16
    and another covering z17-18 over the same area become one source
    covering z12-18.

    Tile paths are validated and written under remapped source folders.

    :param input_paths: List of paths to input ``.smp`` files (must be >= 2)
    :param output_path: Path for the output merged ``.smp`` file
    :param feedback: Optional QGIS-style feedback object with ``isCanceled()``
        and ``pushInfo()`` methods
    :returns: Path to the merged SMP file on success, ``None`` on cancellation
    :raises ValueError: If fewer than 2 inputs or inputs are invalid
    """
    if len(input_paths) < 2:
        raise ValueError("At least 2 SMP files are required for merging.")

    # Guard: output must not overwrite any input file
    abs_output_path = os.path.abspath(output_path)
    for input_path in input_paths:
        if os.path.abspath(input_path) == abs_output_path:
            raise ValueError(
                "Output SMP file must be different from every input file."
            )

    if feedback and hasattr(feedback, 'pushInfo'):
        feedback.pushInfo("Merging {} SMP files...".format(len(input_paths)))

    # Phase 1: Parse all input style.json files
    input_styles = []
    for path in input_paths:
        if not os.path.isfile(path):
            raise ValueError(
                "Input file does not exist: {}".format(path)
            )
        style = _read_smp_style(path)
        input_styles.append(style)

    input_export_bounds = [
        bounds for bounds in (_style_export_bounds(style)
                              for style in input_styles)
        if bounds is not None
    ]

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Phase 2: Collect all source candidates with metadata
    # Each candidate is a dict describing one source from one input
    candidates = []
    input_source_index_to_id = {}

    for input_idx, style in enumerate(input_styles):
        sources = style.get('sources', {})
        index_to_id = {}
        for source_id, source_def in sources.items():
            tiles = source_def.get('tiles', [])
            if not tiles:
                raise ValueError(
                    "Source '{}' is missing an SMP tile URI.".format(
                        source_id)
                )
            try:
                old_src_idx, ext = _parse_smp_uri(tiles[0])
            except ValueError:
                raise ValueError(
                    "Unsupported non-SMP tile URI for source '{}': "
                    "{}".format(source_id, tiles[0])
                )
            index_to_id[old_src_idx] = source_id

            candidates.append({
                'input_idx': input_idx,
                'source_id': source_id,
                'source_def': source_def,
                'ext': ext,
                'bounds': source_def.get('bounds'),
                'minzoom': source_def.get('minzoom', 0),
                'maxzoom': source_def.get('maxzoom', 0),
            })

        input_source_index_to_id[input_idx] = index_to_id

    # Phase 3: Find groups of mergeable sources
    groups = _find_mergeable_sources(candidates)

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Phase 4: Build merged sources, layers, and remapping
    merged_sources = {}
    merged_layers = [
        {
            "id": "background",
            "type": "background",
            "paint": {"background-color": "white"},
        }
    ]
    merged_source_folders = {}
    max_maxzoom = 0
    used_source_ids = set()
    next_source_index = 0

    # Mapping: (input_idx, old_source_id) ->
    #   (new_source_id, new_source_index)
    source_remap = {}

    # Track which layer source IDs map to which merged source,
    # so we can consolidate layers too
    # merged_source_id -> list of (input_idx, original_source_id)
    merged_source_origins = {}

    for group in groups:
        # Pick the first candidate as the "primary" — its source_id
        # becomes the base for the merged source
        primary = candidates[group[0]]
        merged_minzoom = min(candidates[i]['minzoom'] for i in group)
        merged_maxzoom = max(candidates[i]['maxzoom'] for i in group)
        merged_bounds_list = [
            candidates[i]['bounds'] for i in group
            if candidates[i]['bounds'] is not None
        ]
        merged_bounds = _union_bounds(merged_bounds_list) \
            if merged_bounds_list else None

        # Determine the merged source ID
        new_source_id = _dedom(primary['source_id'], used_source_ids)
        new_source_index = next_source_index
        next_source_index += 1

        # Build the merged source definition
        new_source_def = dict(primary['source_def'])
        new_source_def['minzoom'] = merged_minzoom
        new_source_def['maxzoom'] = merged_maxzoom
        if merged_bounds is not None:
            new_source_def['bounds'] = merged_bounds
        new_source_def['tiles'] = [
            _make_smp_uri(new_source_index, primary['ext'])
        ]
        merged_sources[new_source_id] = new_source_def
        source_code = _encode_source_index(new_source_index)
        merged_source_folders[new_source_id] = "s/{}".format(source_code)

        if merged_maxzoom > max_maxzoom:
            max_maxzoom = merged_maxzoom

        # Record origins for layer consolidation
        merged_source_origins[new_source_id] = []

        # Set up remapping for all sources in the group
        for idx in group:
            c = candidates[idx]
            source_remap[(c['input_idx'], c['source_id'])] = (
                new_source_id, new_source_index
            )
            merged_source_origins[new_source_id].append(
                (c['input_idx'], c['source_id'])
            )

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Phase 5: Remap layers — consolidate layers that point to the same
    # merged source
    # For each merged source, keep only ONE non-background layer
    # (the first one encountered), skip duplicates
    seen_merged_source_layers = set()  # merged_source_id set

    for input_idx, style in enumerate(input_styles):
        layers = style.get('layers', [])
        for layer in layers:
            if layer.get('type') == 'background':
                continue
            layer_source = layer.get('source')
            if not layer_source:
                continue
            if (input_idx, layer_source) not in source_remap:
                continue

            new_source_id, _ = source_remap[(input_idx, layer_source)]

            # Skip if we already have a layer for this merged source
            if new_source_id in seen_merged_source_layers:
                continue
            seen_merged_source_layers.add(new_source_id)

            new_layer = dict(layer)
            new_layer['source'] = new_source_id
            # Deduplicate layer IDs
            layer_id = new_layer.get('id', '')
            if layer_id:
                base_id = layer_id
                suffix = 2
                existing_ids = {ml.get('id') for ml in merged_layers}
                while layer_id in existing_ids:
                    layer_id = "{}_{}".format(base_id, suffix)
                    suffix += 1
                new_layer['id'] = layer_id
            merged_layers.append(new_layer)

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Compute smp:bounds from each input style's declared export bounds. This
    # preserves generated SMP local/detail metadata while still giving a useful
    # union for older hand-built SMPs that only have source-level bounds.
    merged_meta_bounds = (
        _union_bounds(input_export_bounds)
        if input_export_bounds else [-180, -85.0511, 180, 85.0511]
    )

    # Build center from merged bounds
    center_lon = (merged_meta_bounds[0] + merged_meta_bounds[2]) / 2
    center_lat = (merged_meta_bounds[1] + merged_meta_bounds[3]) / 2

    merged_style = {
        "version": 8,
        "name": "Merged SMP",
        "sources": merged_sources,
        "layers": merged_layers,
        "metadata": {
            "smp:bounds": merged_meta_bounds,
            "smp:maxzoom": max_maxzoom,
            "smp:sourceFolders": merged_source_folders,
        },
        "center": [center_lon, center_lat],
        "zoom": min(max_maxzoom, 11),
    }

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Phase 6: Collect all tile entries with remapped paths
    # tile_entries: list of (input_path, old_arcname, new_arcname)
    tile_entries = []
    for input_idx, smp_path in enumerate(input_paths):
        index_to_id = input_source_index_to_id[input_idx]
        try:
            zf = zipfile.ZipFile(smp_path, 'r')
        except zipfile.BadZipFile:
            raise ValueError(
                "File is not a valid ZIP/SMP archive: {}".format(smp_path)
            )
        with zf:
            for info in zf.infolist():
                if not info.filename.startswith('s/') or info.is_dir():
                    continue
                parts = info.filename.split('/')
                if len(parts) < 5:
                    continue
                # Security: reject path traversal components
                if '..' in parts or any(not p for p in parts):
                    continue
                try:
                    old_source_index = _decode_source_index(parts[1])
                except ValueError:
                    continue
                old_source_id = index_to_id.get(old_source_index)
                if old_source_id is None:
                    continue

                _, new_source_index = source_remap[
                    (input_idx, old_source_id)
                ]
                old_source_code = _encode_source_index(old_source_index)
                new_source_code = _encode_source_index(new_source_index)
                old_prefix = "s/{}/".format(old_source_code)
                new_prefix = "s/{}/".format(new_source_code)
                new_arcname = info.filename.replace(
                    old_prefix, new_prefix, 1
                )
                # Security: validate the remapped path is canonical
                if not _is_safe_tile_path(new_arcname):
                    continue
                tile_entries.append(
                    (smp_path, info.filename, new_arcname)
                )

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        return None

    # Phase 7: Write the merged archive
    style_json_bytes = json.dumps(merged_style, indent=2).encode('utf-8')

    _write_merged_archive(
        style_json_bytes, tile_entries, output_path, feedback
    )

    if feedback and hasattr(feedback, 'isCanceled') and feedback.isCanceled():
        try:
            os.unlink(output_path)
        except OSError:
            pass
        return None

    if feedback and hasattr(feedback, 'pushInfo'):
        total_sources = len(merged_sources)
        total_layers = len(merged_layers) - 1  # exclude background
        feedback.pushInfo(
            "Merge complete: {} sources, {} layers, {} tile entries".format(
                total_sources, total_layers, len(tile_entries)
            )
        )

    return output_path


def _write_merged_archive(style_json_bytes, tile_entries, output_path,
                          feedback=None):
    """Write the merged SMP ZIP archive.

    The writer uses the standard ``zipfile`` module so local headers and central
    directory entries stay consistent for all readers. If two inputs resolve to
    the same output tile path, identical content is written once and conflicting
    content raises an error.

    :param style_json_bytes: Encoded style.json content
    :param tile_entries: List of (smp_path, old_arcname, new_arcname)
    :param output_path: Destination file path
    :param feedback: Optional QGIS feedback object
    """
    if len(tile_entries) + 2 >= 65535:
        raise ValueError(
            "Archive has {} entries, which exceeds the ZIP format limit "
            "of 65534. Reduce the number of tiles.".format(
                len(tile_entries) + 2
            )
        )

    cancelled = False
    written_tile_hashes = {}
    input_zips = {}
    try:
        with zipfile.ZipFile(output_path, 'w') as out_zf:
            if feedback and hasattr(feedback, 'isCanceled') \
                    and feedback.isCanceled():
                cancelled = True
            else:
                out_zf.writestr(
                    'style.json', style_json_bytes,
                    compress_type=zipfile.ZIP_DEFLATED
                )
                out_zf.writestr('VERSION', '1.0')

            if not cancelled:
                for smp_path, old_arcname, new_arcname in tile_entries:
                    if feedback and hasattr(feedback, 'isCanceled') \
                            and feedback.isCanceled():
                        cancelled = True
                        break
                    in_zf = input_zips.get(smp_path)
                    if in_zf is None:
                        in_zf = zipfile.ZipFile(smp_path, 'r')
                        input_zips[smp_path] = in_zf
                    data = in_zf.read(old_arcname)
                    content_hash = hashlib.sha256(data).hexdigest()
                    previous_hash = written_tile_hashes.get(new_arcname)
                    if previous_hash is not None:
                        if previous_hash != content_hash:
                            raise ValueError(
                                "Conflicting tile content for output path: "
                                "{}".format(new_arcname)
                            )
                        continue
                    written_tile_hashes[new_arcname] = content_hash
                    out_zf.writestr(
                        new_arcname, data,
                        compress_type=zipfile.ZIP_STORED
                    )
    except Exception:
        try:
            os.unlink(output_path)
        except OSError:
            pass
        raise

    finally:
        for in_zf in input_zips.values():
            in_zf.close()

    if cancelled:
        try:
            os.unlink(output_path)
        except OSError:
            pass
