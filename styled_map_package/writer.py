"""Writer for Styled Map Package (``.smp``) archives."""

import copy
import functools
import hashlib
import io
import json
import os
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errors import (
    DuplicateEntryError,
    MissingFontsError,
    MissingSourcesError,
    MissingSpriteError,
    SourceNotFoundError,
    TileFormatMismatchError,
    UnsupportedSourceTypeError,
)
from ._fileformats import detect_tile_format
from ._geo import (
    MAX_BOUNDS,
    SphericalMercator,
    bbox_2d,
    geojson_bbox,
    tile_to_bbox,
    union_bbox,
)
from ._style import replace_font_stacks
from ._templates import (
    FONTS_FOLDER,
    FORMAT_VERSION,
    GLYPH_URI,
    SOURCES_FOLDER,
    STYLE_FILE,
    VERSION_FILE,
    encode_source_id,
    get_glyph_filename,
    get_sprite_filename,
    get_sprite_uri,
    get_tile_filename,
    get_tile_uri,
)

SUPPORTED_SOURCE_TYPES = ("raster", "vector", "geojson")

# Image and pre-gzipped resources are already compressed, so they are stored
# (ZIP method 0). Everything else is deflated (method 8). See spec §3.3.
_STORE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gz")


def _should_store(name):
    return name.endswith(_STORE_SUFFIXES)


@dataclass
class _SourceInfo:
    source: dict
    encoded_id: str
    format: Optional[str] = None
    min_zoom: Optional[int] = None
    # zoom -> [minX, minY, maxX, maxY]; used to infer smp:bufferTiles.
    tile_extents: Dict[int, List[int]] = field(default_factory=dict)


@dataclass
class _Entry:
    name: str
    store: bool
    data: Optional[bytes] = None
    path: Optional[str] = None  # read lazily at write time when set

    def read(self):
        if self.data is not None:
            return self.data
        with open(self.path, "rb") as fh:
            return fh.read()


def _is_empty_feature_collection(data):
    return (
        isinstance(data, dict)
        and data.get("type") == "FeatureCollection"
        and len(data.get("features", [])) == 0
    )


def _resolve_source(data):
    """Return ``("bytes", b)`` or ``("path", p)`` for a binary resource input.

    Accepts ``bytes``/``bytearray``/``memoryview``, a filesystem path
    (``str``/``os.PathLike``), or a binary file-like object (read immediately).
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        return ("bytes", bytes(data))
    if isinstance(data, (str, os.PathLike)):
        return ("path", os.fspath(data))
    if hasattr(data, "read"):
        return ("bytes", data.read())
    raise TypeError(
        "Expected bytes, a file path, or a binary file-like object, got "
        + type(data).__name__
    )


def _header_bytes(resolved, n=12):
    kind, value = resolved
    if kind == "bytes":
        return value[:n]
    with open(value, "rb") as fh:
        return fh.read(n)


class Writer:
    """Assemble a Styled Map Package from a MapLibre style and its resources.

    Typical use::

        writer = Writer(style)
        writer.add_tile(tile_bytes, z=0, x=0, y=0, source_id="my-source")
        writer.add_sprite(json=sprite_index, png=sprite_png)
        writer.add_glyphs(glyph_bytes, font="Open Sans Regular", range="0-255")
        writer.save("map.smp")

    The input ``style`` must be a valid MapLibre **v8** style. This writer does
    **not** migrate v7 styles or validate the style (unlike the JavaScript
    reference implementation); the caller is responsible for supplying a valid
    style. The style object is deep-copied and never mutated.

    Sources are created implicitly the first time a tile is added for them, or
    eagerly for ``geojson`` sources declared in the style. Adding a tile for a
    source that is not declared in the style raises :class:`SourceNotFoundError`.

    :param dedupe: When ``True``, entries with byte-identical content are stored
        only once; additional entries in the central directory alias the shared
        data (spec §3.6). This shrinks archives with many repeated tiles (e.g.
        empty ocean tiles). **Trade-off:** such archives are not readable by
        many general-purpose ZIP tools (macOS Finder, Info-ZIP, Go's
        ``archive/zip``); they remain fully readable by this package's
        :class:`~styled_map_package.Reader` and the JavaScript reference reader.
        Defaults to ``False``.
    """

    def __init__(self, style, *, dedupe=False):
        self._style = copy.deepcopy(style)
        self._dedupe = dedupe
        self._sources: Dict[str, _SourceInfo] = {}
        self._entries: List[_Entry] = []
        self._added_names = set()
        self._fonts = set()
        self._sprite_ids = set()
        self._finished = False

        # Eagerly add GeoJSON sources. If they reference data via a URL and no
        # data is inlined, they become empty and are dropped at finish().
        for source_id, source in self._style.get("sources", {}).items():
            if isinstance(source, dict) and source.get("type") == "geojson":
                self._add_source(source_id, source)

    # -- adding resources -------------------------------------------------

    def add_tile(self, data, *, z, x, y, source_id, format=None):
        """Add a tile. Coordinates use the XYZ scheme (origin top-left / NW).

        :param data: Tile bytes, a file path, or a binary file-like object.
        :param z: Zoom level.
        :param x: Tile column (XYZ).
        :param y: Tile row (XYZ). For TMS tiles convert first with
            :func:`styled_map_package.tms_to_xyz_y`.
        :param source_id: ID of a tile source declared in the style.
        :param format: One of ``"mvt"``, ``"png"``, ``"jpg"``, ``"webp"``.
            Auto-detected from the tile's magic bytes when omitted.

        Raises :class:`SourceNotFoundError`, :class:`UnsupportedSourceTypeError`,
        :class:`TileFormatMismatchError` or :class:`DuplicateEntryError`.
        """
        resolved = _resolve_source(data)

        info = self._sources.get(source_id)
        if info is None:
            source = self._style.get("sources", {}).get(source_id)
            if source is None:
                raise SourceNotFoundError(
                    "Source not referenced in style.json: {0}".format(source_id)
                )
            if source.get("type") not in ("raster", "vector"):
                raise UnsupportedSourceTypeError(
                    "Unsupported source type: {0}".format(source.get("type"))
                )
            info = self._add_source(source_id, source)

        if format is None:
            format = detect_tile_format(_header_bytes(resolved))

        if info.format is None:
            info.format = format
        elif info.format != format:
            raise TileFormatMismatchError(
                "Tile format mismatch for source {0}: expected {1}, got {2}".format(
                    source_id, info.format, format
                )
            )

        if info.min_zoom is None or z < info.min_zoom:
            info.min_zoom = z

        smp = info.source
        bbox = tile_to_bbox(x, y, z)
        # Bounds are derived from the tiles at the maximum zoom level, because at
        # lower zooms a single tile covers a much larger area than the real data.
        if z > smp["maxzoom"]:
            smp["maxzoom"] = z
            smp["bounds"] = bbox
        elif z == smp["maxzoom"]:
            smp["bounds"] = union_bbox([smp["bounds"], bbox])

        extent = info.tile_extents.get(z)
        if extent is None:
            info.tile_extents[z] = [x, y, x, y]
        else:
            if x < extent[0]:
                extent[0] = x
            if y < extent[1]:
                extent[1] = y
            if x > extent[2]:
                extent[2] = x
            if y > extent[3]:
                extent[3] = y

        name = get_tile_filename(info.encoded_id, z, x, y, format)
        # Tiles are always stored uncompressed: their formats are already
        # compressed (gzip for mvt, image codecs for raster).
        self._add_entry(name, resolved, store=True)

    def add_glyphs(self, data, *, font, range):
        """Add a glyph PBF file for ``font`` and Unicode ``range`` (e.g. ``"0-255"``).

        :param data: Gzip-compressed glyph PBF bytes (``.pbf.gz``), a file path,
            or a binary file-like object.
        """
        self._fonts.add(font)
        name = get_glyph_filename(font, range)
        self._add_entry(name, _resolve_source(data), store=True)

    def add_sprite(self, *, json, png, pixel_ratio=1, id="default"):
        """Add a sprite index and sheet for sprite ``id`` at ``pixel_ratio``.

        :param json: Sprite index — a ``dict`` (JSON-encoded automatically),
            a JSON string, or bytes.
        :param png: Sprite sheet PNG bytes, a file path, or a file-like object.
        :param pixel_ratio: 1 for standard, 2 for ``@2x``, etc.
        :param id: Sprite ID (``"default"`` for the string form of ``sprite``).
        """
        self._sprite_ids.add(id)
        json_name = get_sprite_filename(id, pixel_ratio, ".json")
        png_name = get_sprite_filename(id, pixel_ratio, ".png")
        self._add_entry(json_name, ("bytes", _json_bytes(json)), store=False)
        self._add_entry(png_name, _resolve_source(png), store=True)

    # -- finishing --------------------------------------------------------

    def finish(self):
        """Finalize SMP transforms on the style. Idempotent; called by :meth:`save`.

        Raises :class:`MissingSourcesError`, :class:`MissingFontsError` or
        :class:`MissingSpriteError` if the archive is incomplete.
        """
        if self._finished:
            return
        self._prepare_style()
        self._finished = True

    def save(self, dest):
        """Write the archive to ``dest`` (a path or binary file-like object)."""
        self.finish()
        if hasattr(dest, "write"):
            self._write_archive(dest)
        else:
            with open(dest, "wb") as fh:
                self._write_archive(fh)

    def to_bytes(self):
        """Return the complete archive as ``bytes``."""
        buf = io.BytesIO()
        self.save(buf)
        return buf.getvalue()

    @property
    def style(self):
        """The transformed style dict (only meaningful after :meth:`finish`)."""
        return self._style

    # -- internals --------------------------------------------------------

    def _add_source(self, source_id, source):
        encoded = encode_source_id(len(self._sources))
        source_type = source.get("type")
        if source_type in ("raster", "vector"):
            smp = {
                k: v for k, v in source.items() if k not in ("tiles", "url", "scheme")
            }
            smp["scheme"] = "xyz"
            smp["minzoom"] = 0
            smp["maxzoom"] = 0
            smp["bounds"] = list(MAX_BOUNDS)
            smp["tiles"] = []
        else:  # geojson
            smp = copy.deepcopy(source)
            # GeoJSON sources carry no tile maxzoom; smp:maxzoom falls back to
            # the default GeoJSON render zoom (16) in _get_max_zoom().
            smp["maxzoom"] = 0
            data = source.get("data")
            if isinstance(data, str):
                # URL-referenced data is not inlined; start empty so the source
                # is dropped at finish() unless the caller inlines the data.
                smp["data"] = {
                    "type": "FeatureCollection",
                    "features": [],
                    "bbox": [0, 0, 0, 0],
                }
            else:
                inlined = copy.deepcopy(data) if data else {}
                if not inlined.get("bbox"):
                    computed = geojson_bbox(inlined)
                    if computed is not None:
                        inlined["bbox"] = computed
                smp["data"] = inlined
        info = _SourceInfo(source=smp, encoded_id=encoded)
        self._sources[source_id] = info
        return info

    def _add_entry(self, name, resolved, store):
        name = unicodedata.normalize("NFC", name)
        if name in self._added_names:
            raise DuplicateEntryError("{0} already added".format(name))
        self._added_names.add(name)
        kind, value = resolved
        if kind == "bytes":
            self._entries.append(_Entry(name=name, store=store, data=value))
        else:
            self._entries.append(_Entry(name=name, store=store, path=value))

    def _get_bounds(self):
        bounds = None
        maxzoom = 0
        for info in self._sources.values():
            smp = info.source
            if smp.get("type") == "geojson":
                data = smp.get("data")
                if _is_empty_feature_collection(data):
                    continue
                raw_bbox = data.get("bbox") if isinstance(data, dict) else None
                if raw_bbox is None:
                    continue
                bbox = bbox_2d(raw_bbox)
                bounds = union_bbox([bounds, bbox]) if bounds else list(bbox)
            else:
                if smp["maxzoom"] < maxzoom:
                    continue
                if smp["maxzoom"] == maxzoom:
                    bounds = (
                        union_bbox([bounds, smp["bounds"]]) if bounds else smp["bounds"]
                    )
                else:
                    bounds = smp["bounds"]
                    maxzoom = smp["maxzoom"]
        return bounds

    def _get_max_zoom(self):
        maxzoom = 0
        for info in self._sources.values():
            smp = info.source
            if smp.get("type") == "geojson":
                source_maxzoom = smp.get("maxzoom") or 16
            else:
                source_maxzoom = smp["maxzoom"]
            maxzoom = max(maxzoom, source_maxzoom)
        return maxzoom

    def _get_buffer_tiles(self):
        sm = SphericalMercator(size=256)
        buffer_tiles = 0
        for info in self._sources.values():
            smp = info.source
            if smp.get("type") not in ("raster", "vector"):
                continue
            for z, extent in info.tile_extents.items():
                if z >= smp["maxzoom"]:
                    continue
                b = sm.xyz(list(smp["bounds"]), z)
                ring = max(
                    b["minX"] - extent[0],
                    extent[2] - b["maxX"],
                    b["minY"] - extent[1],
                    extent[3] - b["maxY"],
                    0,
                )
                buffer_tiles = max(buffer_tiles, ring)
        return buffer_tiles

    def _prepare_style(self):
        if len(self._sources) == 0:
            raise MissingSourcesError("Missing sources: add at least one source")
        style = self._style

        if style.get("glyphs") and len(self._fonts) == 0:
            raise MissingFontsError(
                "Missing fonts: style references glyphs but no fonts added"
            )

        replace_font_stacks(style, list(self._fonts))

        if style.get("glyphs"):
            style["glyphs"] = GLYPH_URI

        sprite = style.get("sprite")
        if isinstance(sprite, str):
            if "default" not in self._sprite_ids:
                raise MissingSpriteError(
                    "Missing sprite: style references sprite but none added"
                )
            style["sprite"] = get_sprite_uri()
        elif isinstance(sprite, list):
            new_sprite = []
            for item in sprite:
                sprite_id = item.get("id")
                if sprite_id not in self._sprite_ids:
                    raise MissingSpriteError(
                        "Missing sprite: style references sprite {0} but none "
                        "added".format(sprite_id)
                    )
                new_sprite.append({"id": sprite_id, "url": get_sprite_uri(sprite_id)})
            style["sprite"] = new_sprite

        style["sources"] = {}
        for source_id, info in self._sources.items():
            smp = info.source
            if smp.get("type") == "geojson" and _is_empty_feature_collection(
                smp.get("data")
            ):
                continue
            style["sources"][source_id] = smp
            if "tiles" in smp:
                smp["tiles"] = [get_tile_uri(info.encoded_id, info.format or "mvt")]
                # Track the actual minimum zoom of added tiles so the source's
                # zoom range matches the tiles present (spec §5.6/§5.8). The JS
                # reference always emits 0; tracking the real value keeps stacked
                # multi-source packages (e.g. world/region/local) spec-compliant.
                if info.min_zoom is not None:
                    smp["minzoom"] = info.min_zoom

        layers = style.get("layers", [])
        style["layers"] = [
            layer
            for layer in layers
            if not isinstance(layer, dict)
            or "source" not in layer
            or style["sources"].get(layer["source"])
        ]

        metadata = style.get("metadata")
        if metadata is None:
            metadata = {}
            style["metadata"] = metadata
        bounds = self._get_bounds()
        if bounds is not None:
            metadata["smp:bounds"] = bounds
            w, s, e, n = bounds
            style["center"] = [w + (e - w) / 2, s + (n - s) / 2]
        metadata["smp:maxzoom"] = self._get_max_zoom()
        buffer_tiles = self._get_buffer_tiles()
        if buffer_tiles > 0:
            metadata["smp:bufferTiles"] = buffer_tiles
        metadata["smp:sourceFolders"] = {
            source_id: SOURCES_FOLDER + "/" + info.encoded_id
            for source_id, info in self._sources.items()
        }
        style["zoom"] = max(0, self._get_max_zoom() - 2)

    def _build_entries(self):
        entries = list(self._entries)
        version_bytes = FORMAT_VERSION.encode("utf-8")
        entries.append(_Entry(name=VERSION_FILE, store=False, data=version_bytes))
        style_bytes = json.dumps(
            self._style, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        entries.append(_Entry(name=STYLE_FILE, store=False, data=style_bytes))
        entries.sort(key=functools.cmp_to_key(_compare_entry_names))
        return entries

    def _write_archive(self, fileobj):
        _write_entries(fileobj, self._build_entries(), self._dedupe)


@dataclass
class ArchiveEntry:
    """A single entry for :func:`write_smp_archive`.

    Provide exactly one of ``data`` (raw bytes) or ``path`` (a filesystem path
    read lazily at write time). ``store`` forces ZIP store mode when ``True`` or
    deflate when ``False``; when ``None`` it is chosen from the file extension
    (images and ``.gz`` files are stored, everything else is deflated).
    """

    name: str
    data: Optional[bytes] = None
    path: Optional[str] = None
    store: Optional[bool] = None


def write_smp_archive(dest, entries, *, dedupe=False):
    """Write ``entries`` into a spec-ordered SMP ZIP archive at ``dest``.

    This is the low-level archive engine used by :class:`Writer`, exposed for
    callers that build ``style.json`` and lay out resources themselves (for
    example the QGIS plugin, which renders its own tiles). It does not transform
    a style — it only assembles the given entries into a conforming archive:

    - entries are ordered per spec §3.2 (``VERSION``, ``style.json``, each
      font's ``0-255`` range, then tiles by ascending zoom, then the rest);
    - compression follows spec §3.3 (already-compressed resources stored, the
      rest deflated) unless an entry overrides it via ``store``;
    - ZIP64 is emitted automatically when required (spec §3.5);
    - ``dedupe=True`` stores byte-identical entries once via central-directory
      aliasing (spec §3.6; see :class:`Writer` for the compatibility caveat).

    :param dest: Output path or a binary file-like object.
    :param entries: Iterable of :class:`ArchiveEntry`.
    :param dedupe: Enable content deduplication.

    Raises :class:`DuplicateEntryError` if two entries share a name, and
    ``ValueError`` if an entry has neither ``data`` nor ``path``.
    """
    prepared = []
    seen_names = set()
    for entry in entries:
        name = unicodedata.normalize("NFC", entry.name)
        if name in seen_names:
            raise DuplicateEntryError("{0} already added".format(name))
        seen_names.add(name)
        if entry.data is None and entry.path is None:
            raise ValueError(
                "ArchiveEntry {0!r} must have either data or path".format(name)
            )
        store = _should_store(name) if entry.store is None else entry.store
        prepared.append(
            _Entry(name=name, store=store, data=entry.data, path=entry.path)
        )
    prepared.sort(key=functools.cmp_to_key(_compare_entry_names))
    if hasattr(dest, "write"):
        _write_entries(dest, prepared, dedupe)
    else:
        with open(dest, "wb") as fh:
            _write_entries(fh, prepared, dedupe)


def _write_entries(fileobj, entries, dedupe):
    # (store, sha256) -> the ZipInfo of the first entry written with that
    # content. Only populated when dedupe is enabled.
    seen = {}
    with zipfile.ZipFile(fileobj, "w", allowZip64=True) as zf:
        for entry in entries:
            content = entry.read()
            if dedupe:
                key = (entry.store, hashlib.sha256(content).digest())
                original = seen.get(key)
                if original is not None:
                    _append_alias(zf, entry.name, original)
                    continue
            info = zipfile.ZipInfo(filename=entry.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = (
                zipfile.ZIP_STORED if entry.store else zipfile.ZIP_DEFLATED
            )
            info.external_attr = 0o644 << 16
            zf.writestr(info, content)
            if dedupe:
                seen[key] = info


# Fields copied from an already-written entry to an aliased central-directory
# entry. The alias has its own name but shares the original's local file header
# (offset) and data (CRC/sizes/method). zipfile re-derives the UTF-8 filename
# flag per name, and computes the ZIP64 extra from the copied offset/sizes.
_ALIAS_FIELDS = (
    "header_offset",
    "CRC",
    "compress_size",
    "file_size",
    "compress_type",
    "flag_bits",
    "create_system",
    "extract_version",
    "date_time",
    "external_attr",
    "internal_attr",
)


def _append_alias(zf, name, original):
    """Add a central-directory-only entry ``name`` that aliases ``original``.

    No new local header or data is written; the new entry points at the
    original's local file header (spec §3.6 deduplication).
    """
    alias = zipfile.ZipInfo(filename=name)
    for attr in _ALIAS_FIELDS:
        setattr(alias, attr, getattr(original, attr))
    zf.filelist.append(alias)
    zf.NameToInfo[name] = alias


def _json_bytes(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _compare_entry_names(a_entry, b_entry):
    """Order entries for optimal read performance (port of writer.js sortEntries).

    VERSION first, then style.json, then each font's 0-255 range, then tiles
    ordered by ascending zoom, then everything else.
    """
    a = a_entry.name
    b = b_entry.name
    if a == VERSION_FILE:
        return -1
    if b == VERSION_FILE:
        return 1
    if a == STYLE_FILE:
        return -1
    if b == STYLE_FILE:
        return 1
    fa = a.split("/")
    fb = b.split("/")
    a_first = fa[0] == FONTS_FOLDER and len(fa) > 2 and fa[2] == "0-255.pbf.gz"
    b_first = fb[0] == FONTS_FOLDER and len(fb) > 2 and fb[2] == "0-255.pbf.gz"
    if a_first and b_first:
        return -1 if a < b else (1 if a > b else 0)
    if a_first:
        return -1
    if b_first:
        return 1
    a_src = fa[0] == SOURCES_FOLDER
    b_src = fb[0] == SOURCES_FOLDER
    if a_src and not b_src:
        return -1
    if b_src and not a_src:
        return 1
    if (
        a_src
        and b_src
        and len(fa) > 2
        and len(fb) > 2
        and fa[2].isdigit()
        and fb[2].isdigit()
    ):
        # Both are tiles (s/<id>/<z>/...): order by ascending zoom. Non-tile
        # entries under s/ (e.g. s/<id>/data.geojson) fall through to name order.
        return int(fa[2]) - int(fb[2])
    return 0
