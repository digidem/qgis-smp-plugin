# -*- coding: utf-8 -*-

import os
import json
import math
import threading
import hashlib
import struct
import zlib
import shutil
import zipfile
import tempfile
import time
from typing import NamedTuple
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from qgis.core import (
    QgsProject,
    QgsMapSettings,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    Qgis,
    QgsMapRendererCustomPainterJob
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QPainter, QImageWriter

# Warn if estimated tile count exceeds this threshold
TILE_COUNT_WARNING_THRESHOLD = 5000
# Estimated bytes per tile (PNG ~50 KB, JPG ~15 KB)
BYTES_PER_TILE_PNG = 50 * 1024
BYTES_PER_TILE_JPG = 15 * 1024
# Minimum free disk space to keep (100 MB)
MIN_FREE_SPACE_BYTES = 100 * 1024 * 1024
WORLD_BOUNDS_WGS84 = (-180.0, -85.0511, 180.0, 85.0511)
FIXED_SOURCE_SLOTS = (
    {
        'role': 'world',
        'source_id': 'world-overview',
        'source_index': 0,
        'name': 'World Overview',
        'layer_id': 'world-raster',
    },
    {
        'role': 'region',
        'source_id': 'region-detail',
        'source_index': 1,
        'name': 'Region Detail',
        'layer_id': 'region-raster',
    },
    {
        'role': 'local',
        'source_id': 'local-detail',
        'source_index': 2,
        'name': 'Local Detail',
        'layer_id': 'local-raster',
    },
)
SOURCE_SLOT_BY_ID = {
    slot['source_id']: slot for slot in FIXED_SOURCE_SLOTS
}
SOURCE_SLOT_BY_INDEX = {
    slot['source_index']: slot for slot in FIXED_SOURCE_SLOTS
}


class LocalHeaderEntry(NamedTuple):
    offset: int
    arcname: str
    crc: int
    compressed_size: int
    uncompressed_size: int


class HashOffsetEntry(NamedTuple):
    offset: int
    crc: int
    compressed_size: int
    uncompressed_size: int


class TileCache:
    """
    Manages a persistent tile cache with config-based invalidation.

    A JSON sidecar file (`_cache_meta.json`) in `cache_dir` stores a dict
    mapping tile keys ("z/x/y") to the config fingerprint used when that
    tile was last rendered. If the fingerprint for the current run differs,
    the tile is treated as stale and re-rendered.
    """

    META_FILE = '_cache_meta.json'
    _path_states = {}
    _path_states_guard = threading.Lock()

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self._meta_path = os.path.join(cache_dir, self.META_FILE)
        self._state = self._state_for_path(self._meta_path)
        self._lock = self._state['lock']

    @classmethod
    def _state_for_path(cls, path):
        with cls._path_states_guard:
            if path not in cls._path_states:
                cache_dir = os.path.dirname(path)
                cls._path_states[path] = {
                    'lock': threading.Lock(),
                    'meta': cls._load_from_path(path),
                    'dirty': False,
                    'cache_dir': cache_dir,
                }
            return cls._path_states[path]

    @staticmethod
    def _load_from_path(path):
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    data = json.load(fh)
                # Schema migration: discard old-format caches that lack
                # source_index in their keys.
                if data.get('schema_version', 0) < 2:
                    return {}
                return data
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _load(self):
        return self._load_from_path(self._meta_path)

    def _save(self):
        """Write metadata atomically: write to a temp file then rename."""
        tmp_path = self._meta_path + '.tmp'
        try:
            with open(tmp_path, 'w') as fh:
                meta = dict(self._state['meta'])
                meta['schema_version'] = 2
                json.dump(meta, fh)
            os.replace(tmp_path, self._meta_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def make_fingerprint(tile_format, jpeg_quality, *extra_parts):
        """Return a string that identifies the generation config."""
        parts = [str(tile_format), str(jpeg_quality)]
        parts.extend(str(part) for part in extra_parts if part not in (None, ''))
        return ':'.join(parts)

    def is_fresh(self, zoom, x, y, fingerprint, source_index=0):
        """Return True if the cached tile matches the current fingerprint."""
        key = f"{source_index}/{zoom}/{x}/{y}"
        return self._state['meta'].get(key) == fingerprint

    def mark(self, zoom, x, y, fingerprint, defer_save=False, source_index=0):
        """Record that tile (zoom, x, y) was rendered with this fingerprint.

        Thread-safe: acquires the instance lock before mutating shared state.
        """
        key = f"{source_index}/{zoom}/{x}/{y}"
        with self._lock:
            self._state['meta'][key] = fingerprint
            if defer_save:
                self._state['dirty'] = True
            else:
                self._save()
                self._state['dirty'] = False

    def invalidate(self, zoom, x, y, defer_save=False, source_index=0):
        """Remove a tile fingerprint so a future run will re-render it.

        This helper is not used internally today, but it is retained for
        cache-management callers and tests. Thread-safe: acquires the instance
        lock before mutating shared state.
        """
        key = f"{source_index}/{zoom}/{x}/{y}"
        with self._lock:
            self._state['meta'].pop(key, None)
            if defer_save:
                self._state['dirty'] = True
            else:
                self._save()
                self._state['dirty'] = False

    def flush(self):
        """Persist deferred metadata updates, if any."""
        with self._lock:
            if self._state['dirty']:
                self._save()
                self._state['dirty'] = False




class SMPGenerator:
    """
    Class to generate SMP (Styled Map Package) files for CoMapeo
    """

    TILE_FORMAT_PNG = 'PNG'
    TILE_FORMAT_JPG = 'JPG'
    TILE_FORMAT_WEBP = 'WEBP'
    _VALID_TILE_FORMATS = {TILE_FORMAT_PNG, TILE_FORMAT_JPG, TILE_FORMAT_WEBP}

    @staticmethod
    def _tile_extension(tile_format):
        """Return the file extension for a tile format string."""
        fmt = tile_format.upper() if tile_format else 'PNG'
        if fmt == 'JPG':
            return 'jpg'
        if fmt == 'WEBP':
            return 'webp'
        return 'png'

    @staticmethod
    def _qt_image_format(tile_format):
        """Return the Qt image format string for saving."""
        fmt = tile_format.upper() if tile_format else 'PNG'
        if fmt == 'JPG':
            return 'JPEG'
        if fmt == 'WEBP':
            return 'WEBP'
        return 'PNG'

    @classmethod
    def is_tile_format_supported(cls, tile_format):
        """Return True when the runtime can encode the requested output format."""
        fmt = cls.TILE_FORMAT_PNG if tile_format is None else tile_format.upper()
        if fmt not in cls._VALID_TILE_FORMATS:
            return False
        supported_formats = {
            bytes(writer_format).decode('ascii', errors='ignore').upper()
            for writer_format in QImageWriter.supportedImageFormats()
        }
        acceptable_qt_formats = {
            cls.TILE_FORMAT_PNG: {'PNG'},
            cls.TILE_FORMAT_JPG: {'JPG', 'JPEG'},
            cls.TILE_FORMAT_WEBP: {'WEBP'},
        }
        return bool(acceptable_qt_formats[fmt].intersection(supported_formats))

    @classmethod
    def validate_tile_format(cls, tile_format):
        """Normalize and validate a requested tile output format."""
        fmt = cls.TILE_FORMAT_PNG if tile_format is None else tile_format.upper()
        if fmt not in cls._VALID_TILE_FORMATS:
            raise ValueError(
                f"Unsupported tile format: {fmt}. Use 'PNG', 'JPG', or 'WEBP'."
            )
        if not cls.is_tile_format_supported(fmt):
            raise ValueError(
                f"Tile format {fmt} is not supported for output by this runtime."
            )
        return fmt

    # QGIS rendering (QgsMapRendererCustomPainterJob) is not safe to call
    # concurrently from multiple threads.  This lock serialises all
    # map-rendering calls so that only one job is active at a time, while
    # still allowing the ThreadPoolExecutor to parallelise the cheap parts
    # (cache checks, file I/O, progress bookkeeping).
    _render_lock = threading.Lock()

    def __init__(self, feedback=None):
        """
        Initialize the SMP generator

        :param feedback: Feedback object for progress reporting
        """
        self.feedback = feedback

    def log(self, message, level=Qgis.Info):
        """
        Log a message

        :param message: Message to log
        :param level: Log level
        """
        if self.feedback:
            self.feedback.pushInfo(message)
        QgsMessageLog.logMessage(message, 'CoMapeo SMP Generator', level)

    def _deg2num(self, lat_deg, lon_deg, zoom):
        """
        Convert latitude/longitude to tile coordinates at given zoom level
        Based on OpenStreetMap slippy map tilenames standard

        :param lat_deg: Latitude in degrees
        :param lon_deg: Longitude in degrees
        :param zoom: Zoom level
        :return: Tuple of (xtile, ytile)
        """
        lat_rad = math.radians(lat_deg)
        n = 1 << zoom  # 2^zoom
        xtile = int((lon_deg + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return xtile, ytile

    def _num2deg(self, xtile, ytile, zoom):
        """
        Convert tile coordinates to latitude/longitude (NW corner of tile)
        Based on OpenStreetMap slippy map tilenames standard

        :param xtile: Tile X coordinate
        :param ytile: Tile Y coordinate
        :param zoom: Zoom level
        :return: Tuple of (lat_deg, lon_deg) for NW corner
        """
        n = 1 << zoom  # 2^zoom
        lon_deg = xtile / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
        lat_deg = math.degrees(lat_rad)
        return lat_deg, lon_deg

    def get_world_extent(self):
        """Return Web Mercator world bounds transformed to project CRS."""
        wgs84_rect = QgsRectangle(*WORLD_BOUNDS_WGS84)
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        project_crs = QgsProject.instance().crs()
        transform = QgsCoordinateTransform(wgs84, project_crs, QgsProject.instance())
        return transform.transformBoundingBox(wgs84_rect)

    def _get_extent_for_zoom(self, extent, world_extent, zoom,
                             include_world_base_zooms=False, world_max_zoom=3):
        """Return per-zoom extent according to world-base-zooms options."""
        if include_world_base_zooms and zoom <= max(2, world_max_zoom):
            return world_extent
        return extent

    def _get_export_zooms(self, min_zoom, max_zoom,
                          include_world_base_zooms=False, world_max_zoom=3):
        """Return sorted zoom levels to export."""
        zooms = set(range(min_zoom, max_zoom + 1))
        if include_world_base_zooms:
            zooms.update(range(0, max(2, world_max_zoom) + 1))
        return sorted(zooms)

    def _iter_export_ranges(self, extent, min_zoom, max_zoom,
                            include_world_base_zooms=False, world_max_zoom=3):
        """Yield `(zoom, zoom_extent, ranges)` for the effective export plan."""
        world_extent = self.get_world_extent() if include_world_base_zooms else None
        export_zooms = self._get_export_zooms(
            min_zoom, max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom
        )
        for zoom in export_zooms:
            zoom_extent = self._get_extent_for_zoom(
                extent,
                world_extent,
                zoom,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom
            )
            yield zoom, zoom_extent, self._calculate_tiles_at_zoom(zoom_extent, zoom)

    def _build_single_source_plan(self, extent, zoom_list, source_id, source_index,
                                  source_role=None, source_name=None, layer_id=None):
        """Build a per-source export plan with 7-element tiles_by_zoom tuples.

        :param extent: QgsRectangle extent for this source
        :param zoom_list: List of zoom levels to include
        :param source_id: Source identifier string (e.g. "world-overview")
        :param source_index: Integer source index for the fixed source slot
        :param source_role: Optional explicit semantic role override
        :param source_name: Optional explicit display name override
        :param layer_id: Optional explicit style layer id override
        :return: Dict with source_id, source_index, source_bounds, export_zooms,
                 tiles_by_zoom (7-tuples), total_tiles
        """
        tiles_by_zoom = []
        total_tiles = 0

        for zoom in zoom_list:
            for min_x, max_x, min_y, max_y in self._calculate_tiles_at_zoom(extent, zoom):
                num_tiles = (max_x - min_x + 1) * (max_y - min_y + 1)
                tiles_by_zoom.append((zoom, min_x, max_x, min_y, max_y, num_tiles, source_index))
                total_tiles += num_tiles

        source_bounds = list(WORLD_BOUNDS_WGS84) if source_id == 'world-overview' else self._get_bounds_wgs84(extent)
        plan = {
            'source_id': source_id,
            'source_index': source_index,
            'source_bounds': source_bounds,
            'export_zooms': list(zoom_list),
            'tiles_by_zoom': tiles_by_zoom,
            'total_tiles': total_tiles,
        }
        if source_role is not None:
            plan['source_role'] = source_role
        if source_name is not None:
            plan['source_name'] = source_name
        if layer_id is not None:
            plan['layer_id'] = layer_id
        slot = self._source_slot_for_plan(plan)
        plan['source_role'] = slot['role']
        plan['source_name'] = slot['name']
        plan['layer_id'] = slot['layer_id']
        return plan

    @staticmethod
    def _source_slot_for_plan(source_plan):
        source_id = source_plan.get('source_id')
        source_index = source_plan.get('source_index', 0)
        base_slot = SOURCE_SLOT_BY_ID.get(source_id)
        if base_slot is None:
            base_slot = SOURCE_SLOT_BY_INDEX.get(source_index)
        if base_slot is None:
            source_id = source_id or f'source-{source_index}'
            base_slot = {
                'role': f'source-{source_index}',
                'source_id': source_id,
                'source_index': source_index,
                'name': source_id,
                'layer_id': f'source-{source_index}-raster',
            }
        else:
            base_slot = dict(base_slot)

        base_slot['source_id'] = source_id or base_slot['source_id']
        base_slot['source_index'] = source_index
        if 'source_role' in source_plan:
            base_slot['role'] = source_plan['source_role']
        if 'source_name' in source_plan:
            base_slot['name'] = source_plan['source_name']
        if 'layer_id' in source_plan:
            base_slot['layer_id'] = source_plan['layer_id']
        return base_slot

    @staticmethod
    def _root_default_zoom(min_zoom, max_zoom):
        return min(
            max_zoom,
            max(max(min_zoom, 0), min(max_zoom - 2, 11))
        )

    @staticmethod
    def _zoom_gap_levels(export_zooms):
        if not export_zooms:
            return []
        zoom_set = set(export_zooms)
        return [
            zoom for zoom in range(export_zooms[0], export_zooms[-1] + 1)
            if zoom not in zoom_set
        ]

    @staticmethod
    def _longitude_intervals(extent):
        west = extent.xMinimum()
        east = extent.xMaximum()
        if west <= east:
            return [(west, east)]
        return [(west, 180.0), (-180.0, east)]

    @classmethod
    def _extent_contains(cls, outer_extent, inner_extent):
        if outer_extent.yMinimum() > inner_extent.yMinimum():
            return False
        if outer_extent.yMaximum() < inner_extent.yMaximum():
            return False

        outer_intervals = cls._longitude_intervals(outer_extent)
        inner_intervals = cls._longitude_intervals(inner_extent)
        for inner_start, inner_end in inner_intervals:
            if not any(
                outer_start <= inner_start and outer_end >= inner_end
                for outer_start, outer_end in outer_intervals
            ):
                return False
        return True

    def _source_label_map(self, source_plans):
        labels = {
            slot['source_index']: slot['name'] for slot in FIXED_SOURCE_SLOTS
        }
        for source_plan in source_plans or []:
            source_index = source_plan.get('source_index')
            if source_index is None:
                continue
            labels[source_index] = source_plan.get(
                'source_name',
                self._source_slot_for_plan(source_plan)['name']
            )
        return labels

    @classmethod
    def _source_plan_signature(cls, source_plans):
        parts = []
        for source_plan in source_plans:
            bounds = ','.join(str(value) for value in source_plan.get('source_bounds', []))
            export_zooms = source_plan.get('export_zooms', [])
            if export_zooms:
                zoom_signature = f"{export_zooms[0]}-{export_zooms[-1]}"
            else:
                zoom_signature = 'none'
            parts.append(
                f"{source_plan.get('source_index')}:{source_plan.get('source_id')}:{zoom_signature}:{bounds}"
            )
        return '|'.join(parts)

    def _validate_fixed_source_configuration(self, extent, min_zoom, max_zoom,
                                             include_world_base_zooms=False,
                                             world_max_zoom=3,
                                             include_region=False,
                                             region_extent=None,
                                             region_min_zoom=None,
                                             region_max_zoom=None):
        if min_zoom > max_zoom:
            raise ValueError(
                f"Local minimum zoom ({min_zoom}) must not exceed local maximum zoom ({max_zoom})."
            )

        if include_world_base_zooms and world_max_zoom < 0:
            raise ValueError('World maximum zoom must be greater than or equal to 0.')

        if include_region:
            if region_extent is None:
                raise ValueError('Region extent is required when INCLUDE_REGION is enabled.')
            if region_min_zoom is None or region_max_zoom is None:
                raise ValueError(
                    'Region min/max zoom values are required when INCLUDE_REGION is enabled.'
                )
            if region_min_zoom > region_max_zoom:
                raise ValueError(
                    f"Region minimum zoom ({region_min_zoom}) must not exceed region maximum zoom ({region_max_zoom})."
                )
            if not self._extent_contains(region_extent, extent):
                raise ValueError('Local extent must be fully contained within the Region extent.')
            if include_world_base_zooms and world_max_zoom >= region_min_zoom:
                raise ValueError(
                    f"World maximum zoom ({world_max_zoom}) must be less than Region minimum zoom ({region_min_zoom})."
                )
            if region_max_zoom >= min_zoom:
                raise ValueError(
                    f"Region maximum zoom ({region_max_zoom}) must be less than Local minimum zoom ({min_zoom})."
                )
        elif include_world_base_zooms and world_max_zoom >= min_zoom:
            raise ValueError(
                f"World maximum zoom ({world_max_zoom}) must be less than Local minimum zoom ({min_zoom}) when Region is disabled."
            )

    @staticmethod
    def _merged_interval_length(intervals):
        """Return the total length covered by half-open intervals."""
        if not intervals:
            return 0

        sorted_intervals = sorted(intervals)
        total = 0
        start, end = sorted_intervals[0]
        for current_start, current_end in sorted_intervals[1:]:
            if current_start <= end:
                end = max(end, current_end)
            else:
                total += end - start
                start, end = current_start, current_end
        total += end - start
        return total

    @classmethod
    def _count_unique_tiles_in_ranges(cls, tile_ranges):
        """Return the number of unique tiles represented by 7-tuple ranges."""
        ranges_by_zoom = {}
        for zoom, min_x, max_x, min_y, max_y, _, _ in tile_ranges:
            ranges_by_zoom.setdefault(zoom, []).append(
                (min_x, max_x + 1, min_y, max_y + 1)
            )

        total = 0
        for rects in ranges_by_zoom.values():
            events = []
            for min_x, max_x, min_y, max_y in rects:
                events.append((min_x, 1, min_y, max_y))
                events.append((max_x, -1, min_y, max_y))
            events.sort(key=lambda event: event[0])

            active_intervals = {}
            previous_x = None
            index = 0
            while index < len(events):
                current_x = events[index][0]
                if previous_x is not None and current_x > previous_x and active_intervals:
                    covered_y = cls._merged_interval_length(list(active_intervals.keys()))
                    total += (current_x - previous_x) * covered_y

                while index < len(events) and events[index][0] == current_x:
                    _, delta, min_y, max_y = events[index]
                    key = (min_y, max_y)
                    next_count = active_intervals.get(key, 0) + delta
                    if next_count > 0:
                        active_intervals[key] = next_count
                    else:
                        active_intervals.pop(key, None)
                    index += 1
                previous_x = current_x

        return total

    def _build_export_plan(self, extent, min_zoom, max_zoom,
                           include_world_base_zooms=False, world_max_zoom=3,
                           include_region=False, region_extent=None,
                           region_min_zoom=None, region_max_zoom=None):
        """Return a normalized fixed-slot export plan for world/region/local."""
        self._validate_fixed_source_configuration(
            extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom,
            include_region=include_region,
            region_extent=region_extent,
            region_min_zoom=region_min_zoom,
            region_max_zoom=region_max_zoom,
        )

        sources = []

        if include_world_base_zooms:
            world_plan = self._build_single_source_plan(
                self.get_world_extent(),
                list(range(0, world_max_zoom + 1)),
                source_id="world-overview",
                source_index=0,
            )
            sources.append(world_plan)

        if include_region:
            if region_extent is None:
                raise ValueError('Region extent is required when INCLUDE_REGION is enabled.')
            if region_min_zoom is None or region_max_zoom is None:
                raise ValueError('Region min/max zoom values are required when INCLUDE_REGION is enabled.')
            region_plan = self._build_single_source_plan(
                region_extent,
                list(range(region_min_zoom, region_max_zoom + 1)),
                source_id="region-detail",
                source_index=1,
            )
            sources.append(region_plan)

        if not include_world_base_zooms and not include_region:
            local_plan = self._build_single_source_plan(
                extent,
                list(range(min_zoom, max_zoom + 1)),
                source_id="mbtiles-source",
                source_index=0,
                source_role='local',
                source_name='QGIS Map',
                layer_id='raster',
            )
        else:
            local_plan = self._build_single_source_plan(
                extent,
                list(range(min_zoom, max_zoom + 1)),
                source_id="local-detail",
                source_index=2,
            )
        sources.append(local_plan)

        tiles_by_zoom = []
        for src in sources:
            tiles_by_zoom.extend(src['tiles_by_zoom'])

        total_tiles = sum(s['total_tiles'] for s in sources)
        export_zooms = sorted({z for src in sources for z in src['export_zooms']})
        gap_zooms = self._zoom_gap_levels(export_zooms)
        world_coverage_tiles = self._count_unique_tiles_in_ranges(tiles_by_zoom)
        world_tiles = sum(4 ** zoom for zoom in export_zooms)
        return {
            'export_zooms': export_zooms,
            'gap_zooms': gap_zooms,
            'tiles_by_zoom': tiles_by_zoom,
            'total_tiles': total_tiles,
            'world_coverage_tiles': world_coverage_tiles,
            'world_tiles': world_tiles,
            'world_pct': (world_coverage_tiles / world_tiles) * 100 if world_tiles else 0,
            'source_bounds': local_plan['source_bounds'],
            'sources': sources,
        }

    @staticmethod
    def _tile_paths_from_source_plans(source_plans, tile_format):
        """Return manifest paths for tiles across all source plans.

        Each path is prefixed with ``{source_index}/`` so it matches the
        on-disk layout ``tiles_dir/{source_index}/{z}/{x}/{y}.{ext}`` and the
        archive path ``s/{source_index}/{z}/{x}/{y}.{ext}``.
        """
        tile_ext = SMPGenerator._tile_extension(tile_format)
        tile_paths = set()
        for src in source_plans:
            source_index = src['source_index']
            for entry in src['tiles_by_zoom']:
                zoom, min_x, max_x, min_y, max_y, _, _ = entry
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        tile_paths.add(f"{source_index}/{zoom}/{x}/{y}.{tile_ext}")
        return tile_paths

    def estimate_world_tile_count(self, min_zoom, max_zoom):
        """Estimate full-world tile count for a zoom range."""
        return sum(4 ** zoom for zoom in range(min_zoom, max_zoom + 1))

    def estimate_tile_storage_bytes(self, tile_count, tile_format=None):
        """Estimate storage usage in bytes for a tile count and output format."""
        if tile_format == self.TILE_FORMAT_JPG:
            bytes_per_tile = BYTES_PER_TILE_JPG
        elif tile_format == self.TILE_FORMAT_WEBP:
            # WebP is typically 25-35% smaller than JPEG at similar quality
            bytes_per_tile = int(BYTES_PER_TILE_JPG * 0.75)
        else:
            bytes_per_tile = BYTES_PER_TILE_PNG
        return tile_count * bytes_per_tile

    def estimate_mixed_tile_count(self, extent, min_zoom, max_zoom,
                                  include_world_base_zooms=False, world_max_zoom=3,
                                  include_region=False, region_extent=None,
                                  region_min_zoom=None, region_max_zoom=None):
        """Estimate total tiles using the fixed world/region/local source model."""
        return self._build_export_plan(
            extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom,
            include_region=include_region,
            region_extent=region_extent,
            region_min_zoom=region_min_zoom,
            region_max_zoom=region_max_zoom,
        )['total_tiles']

    def estimate_world_pyramid_percentage(self, extent, min_zoom, max_zoom,
                                          include_world_base_zooms=False,
                                          world_max_zoom=3,
                                          include_region=False,
                                          region_extent=None,
                                          region_min_zoom=None,
                                          region_max_zoom=None):
        """Return unique export coverage and full-world pyramid totals."""
        plan = self._build_export_plan(
            extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom,
            include_region=include_region,
            region_extent=region_extent,
            region_min_zoom=region_min_zoom,
            region_max_zoom=region_max_zoom,
        )
        return plan['world_coverage_tiles'], plan['world_tiles'], plan['world_pct']

    def estimate_tile_count(self, extent, min_zoom, max_zoom,
                            include_world_base_zooms=False, world_max_zoom=3,
                            include_region=False, region_extent=None,
                            region_min_zoom=None, region_max_zoom=None):
        """
        Estimate the total number of tiles that will be generated.

        :param extent: QgsRectangle extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: Total estimated tile count
        """
        return self.estimate_mixed_tile_count(
            extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom,
            include_region=include_region,
            region_extent=region_extent,
            region_min_zoom=region_min_zoom,
            region_max_zoom=region_max_zoom,
        )

    def validate_tile_count(self, extent, min_zoom, max_zoom,
                            include_world_base_zooms=False, world_max_zoom=3,
                            include_region=False, region_extent=None,
                            region_min_zoom=None, region_max_zoom=None):
        """
        Check estimated tile count and return (count, warning_message).

        :param extent: QgsRectangle extent
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: Tuple of (tile_count, warning_message_or_None)
        """
        count = self.estimate_tile_count(
            extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=include_world_base_zooms,
            world_max_zoom=world_max_zoom,
            include_region=include_region,
            region_extent=region_extent,
            region_min_zoom=region_min_zoom,
            region_max_zoom=region_max_zoom,
        )
        warning = None

        if count > TILE_COUNT_WARNING_THRESHOLD:
            warning = (
                f"Warning: estimated tile count is {count:,}. "
                f"Generation may take a long time. Consider reducing the extent or zoom range."
            )

        return count, warning

    def validate_disk_space(self, output_path, tile_count, tile_format=None):
        """
        Check that sufficient disk space is available for the tile generation.

        :param output_path: Path to the output SMP file (used to determine disk)
        :param tile_count: Estimated number of tiles
        :param tile_format: Tile format ('PNG' or 'JPG')
        :raises OSError: If insufficient disk space
        """
        estimated_bytes = self.estimate_tile_storage_bytes(tile_count, tile_format)
        estimated_mb = estimated_bytes / (1024 * 1024)

        output_dir = os.path.dirname(os.path.abspath(output_path)) or '.'
        disk_usage = shutil.disk_usage(output_dir)
        free_bytes = disk_usage.free

        self.log(
            f"Disk space: {free_bytes / (1024*1024):.1f} MB free, "
            f"estimated {estimated_mb:.1f} MB needed for tiles"
        )

        required_bytes = estimated_bytes + MIN_FREE_SPACE_BYTES
        if free_bytes < required_bytes:
            raise OSError(
                f"Insufficient disk space. Estimated {estimated_mb:.1f} MB needed, "
                f"but only {free_bytes / (1024*1024):.1f} MB available on disk."
            )

    def validate_extent_size(self, extent, _min_zoom, max_zoom):
        """
        Warn if the extent+zoom combination is unreasonably large.

        :param extent: QgsRectangle extent in project CRS
        :param _min_zoom: Minimum zoom level (reserved for future heuristics)
        :param max_zoom: Maximum zoom level
        :return: Warning message string, or None if extent is acceptable
        """
        bounds = self._get_bounds_wgs84(extent)
        west, south, east, north = bounds

        lon_span = abs(east - west)
        lat_span = abs(north - south)

        # Heuristic: warn if extent is very large at high zoom levels
        # At zoom 14, a single tile covers ~2.4 km. A 1-degree span ≈ 111 km,
        # so 1 degree at zoom 14 ≈ 46 tiles. > 10 degrees at zoom > 12 is suspicious.
        if max_zoom > 12 and (lon_span > 10 or lat_span > 10):
            return (
                f"Warning: large extent ({lon_span:.1f}° wide, {lat_span:.1f}° tall) "
                f"combined with max zoom {max_zoom} may produce excessive tiles. "
                f"Consider reducing the extent or maximum zoom level."
            )

        return None

    def get_tile_grid_rects(self, extent, min_zoom, max_zoom,
                            include_world_base_zooms=False, world_max_zoom=3,
                            include_region=False, region_extent=None,
                            region_min_zoom=None, region_max_zoom=None,
                            export_plan=None):
        """
        Return the WGS84 bounding rectangles of all tiles that would be generated.

        This helper is kept for preview/debug workflows and tests even though
        the current QGIS UI does not yet expose a tile-grid preview. It mirrors
        the fixed world/region/local export plan so callers can inspect the
        final sparse source layout without invoking a render.

        :param extent: QgsRectangle extent in project CRS
        :param min_zoom: Minimum Local zoom level
        :param max_zoom: Maximum Local zoom level
        :return: list of dicts:
                 [{"zoom": z, "x": x, "y": y,
                   "west": w, "south": s, "east": e, "north": n,
                   "source_index": i, "source_id": sid,
                   "source_role": role}, ...]
        """
        if export_plan is None:
            export_plan = self._build_export_plan(
                extent,
                min_zoom,
                max_zoom,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom,
                include_region=include_region,
                region_extent=region_extent,
                region_min_zoom=region_min_zoom,
                region_max_zoom=region_max_zoom,
            )

        rects = []
        source_meta = {}
        for source_plan in export_plan.get('sources', []):
            slot = self._source_slot_for_plan(source_plan)
            source_meta[source_plan['source_index']] = {
                'source_id': source_plan.get('source_id', slot['source_id']),
                'source_role': slot['role'],
            }

        for zoom, min_x, max_x, min_y, max_y, _, source_index in export_plan.get('tiles_by_zoom', []):
            slot_meta = source_meta.get(source_index)
            if slot_meta is None:
                slot = self._source_slot_for_plan({'source_index': source_index})
                slot_meta = {
                    'source_id': slot['source_id'],
                    'source_role': slot['role'],
                }
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    north, west = self._num2deg(x, y, zoom)
                    south, east = self._num2deg(x + 1, y + 1, zoom)
                    rects.append({
                        "zoom": zoom,
                        "x": x,
                        "y": y,
                        "west": west,
                        "south": south,
                        "east": east,
                        "north": north,
                        "source_index": source_index,
                        "source_id": slot_meta['source_id'],
                        "source_role": slot_meta['source_role'],
                    })
        return rects

    def generate_smp_from_canvas(self, extent, min_zoom, max_zoom, output_path,
                                 tile_format=None, jpeg_quality=85, cache_dir=None,
                                 max_workers=None,
                                 include_world_base_zooms=False,
                                 world_max_zoom=3,
                                 include_region=False,
                                 region_extent=None,
                                 region_min_zoom=None,
                                 region_max_zoom=None,
                                 export_plan=None):
        """
        Generate an SMP file from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param output_path: Output path for the SMP file
        :param tile_format: Tile image format ('PNG' or 'JPG'). Defaults to 'PNG'.
        :param jpeg_quality: JPEG compression quality (1-100). Only used when
                             tile_format is 'JPG'. Defaults to 85.
        :param cache_dir: Optional persistent directory for tile cache/resume
        :param max_workers: Number of thread workers. None uses CPU count.
        :return: Path to the generated SMP file
        """
        if tile_format is None:
            tile_format = self.TILE_FORMAT_PNG

        tile_format = self.validate_tile_format(tile_format)

        jpeg_quality = max(1, min(100, int(jpeg_quality)))

        self.log(f"Generating SMP file with zoom levels {min_zoom}-{max_zoom}")
        self.log(f"Extent: {extent.asWktPolygon()}")
        self.log(f"Tile format: {tile_format}" +
                 (f", quality: {jpeg_quality}" if tile_format in (self.TILE_FORMAT_JPG, self.TILE_FORMAT_WEBP) else ""))

        # --- Pre-generation validations ---
        if export_plan is None:
            export_plan = self._build_export_plan(
                extent,
                min_zoom,
                max_zoom,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom,
                include_region=include_region,
                region_extent=region_extent,
                region_min_zoom=region_min_zoom,
                region_max_zoom=region_max_zoom,
            )
        tile_count = export_plan['total_tiles']
        count_warning = None
        if tile_count > TILE_COUNT_WARNING_THRESHOLD:
            count_warning = (
                f"Warning: estimated tile count is {tile_count:,}. "
                f"Generation may take a long time. Consider reducing the extent or zoom range."
            )
        estimated_bytes = self.estimate_tile_storage_bytes(tile_count, tile_format)
        estimated_mb = estimated_bytes / (1024 * 1024)
        self.log(f"Include world base zooms: {include_world_base_zooms}")
        self.log(f"World max zoom: {world_max_zoom}")
        self.log(f"Include region detail: {include_region}")
        if include_region and region_extent is not None:
            self.log(f"Region extent: {region_extent.asWktPolygon()}")
            self.log(f"Region zooms: {region_min_zoom}-{region_max_zoom}")
        self.log(f"Estimated tile count: {tile_count:,}")
        self.log(f"Estimated output size: {estimated_mb:.1f} MB")
        self.log(f"Estimated world pyramid coverage: {export_plan['world_pct']:.2f}%")
        if count_warning:
            self.log(count_warning, Qgis.Warning)

        extent_warning = self.validate_extent_size(extent, min_zoom, max_zoom)
        if extent_warning:
            self.log(extent_warning, Qgis.Warning)

        self.validate_disk_space(output_path, tile_count, tile_format)

        # Create a temporary directory for the SMP contents
        temp_dir = tempfile.mkdtemp()
        self.log(f"Using temporary directory: {temp_dir}")

        try:
            # Create the 's' directory for the style
            style_dir = os.path.join(temp_dir, "s")
            os.makedirs(style_dir, exist_ok=True)

            # Generate the style.json file in the root directory
            source_plans = export_plan.get('sources', [])
            style = self._create_style_from_canvas(
                extent, min_zoom, max_zoom, tile_format,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom,
                source_bounds=export_plan['source_bounds'],
                source_plans=source_plans
            )
            style_path = os.path.join(temp_dir, "style.json")
            with open(style_path, 'w') as f:
                json.dump(style, f, indent=4)

            if cache_dir is not None:
                os.makedirs(cache_dir, exist_ok=True)
                tiles_dir = cache_dir
                tile_cache = TileCache(cache_dir)
            else:
                tiles_dir = style_dir
                tile_cache = None
            self._generate_tiles_from_canvas(
                extent, min_zoom, max_zoom, tiles_dir,
                tile_format=tile_format, jpeg_quality=jpeg_quality,
                resume=(cache_dir is not None),
                max_workers=max_workers,
                tile_cache=tile_cache,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom,
                include_region=include_region,
                region_extent=region_extent,
                region_min_zoom=region_min_zoom,
                region_max_zoom=region_max_zoom,
                export_plan=export_plan
            )
            if tile_cache is not None:
                tile_cache.flush()

            # If the user cancelled during tile generation, do not produce a
            # partial archive.  Return None to signal cancellation to callers.
            if self.feedback and self.feedback.isCanceled():
                self.log("Generation cancelled — no SMP archive created.")
                return None

            # Build the set of tile paths that belong to *this* export so that
            # stale tiles from previous runs are excluded from the archive.
            # Only needed when cache_dir is used (otherwise tiles_dir is fresh).
            tile_paths = None
            if cache_dir is not None:
                tile_paths = self._tile_paths_from_source_plans(source_plans, tile_format)

            # Create the SMP file (zip archive)
            archive_built = self._build_smp_archive(
                style_path=os.path.join(temp_dir, "style.json"),
                tiles_dir=tiles_dir,
                output_path=output_path,
                tile_paths=tile_paths
            )
            if not archive_built:
                self.log("Generation cancelled during archive creation — no SMP archive created.")
                return None

            self.log(f"SMP file generated successfully: {output_path}")
            return output_path  # type: ignore[return-value]

        except Exception as e:
            self.log(f"Error generating SMP file: {str(e)}", Qgis.Critical)
            raise
        finally:
            # Always clean up temporary directory
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                self.log(f"Cleaned up temporary directory: {temp_dir}")

    def _project_title_or_default(self):
        """Return the current project title or the legacy default style name."""
        project = QgsProject.instance()
        title = project.title()
        if isinstance(title, str) and title.strip():
            return title
        base_name = project.baseName()
        if isinstance(base_name, str) and base_name.strip():
            return base_name
        return "QGIS MAP"

    def _create_style_from_canvas(self, extent, min_zoom, max_zoom, tile_format=None,
                                 include_world_base_zooms=False, world_max_zoom=3,
                                 source_bounds=None, source_plans=None):
        """
        Create a MapLibre style JSON from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param tile_format: Tile image format ('PNG' or 'JPG')
        :param source_plans: Optional list of per-source plan dicts for fixed-slot multi-source
        :return: Style JSON object
        """
        if tile_format is None:
            tile_format = self.TILE_FORMAT_PNG

        tile_ext = self._tile_extension(tile_format)

        if source_plans is None:
            export_plan = self._build_export_plan(
                extent,
                min_zoom,
                max_zoom,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom
            )
            source_plans = export_plan.get('sources', [])

        if not source_plans:
            raise ValueError('Source plan is required to create SMP style metadata.')

        local_plan = source_plans[-1]
        local_bounds = local_plan['source_bounds']
        center_lon = (local_bounds[0] + local_bounds[2]) / 2
        center_lat = (local_bounds[1] + local_bounds[3]) / 2
        default_zoom = min(
            max_zoom,
            max(max(min_zoom, 0), min(max_zoom - 2, 11))
        )

        sources = {}
        layers = [
            {
                "id": "background",
                "type": "background",
                "paint": {
                    "background-color": "white"
                }
            }
        ]
        source_folders = {}

        for source_plan in source_plans:
            source_id = source_plan['source_id']
            source_index = source_plan['source_index']
            slot = self._source_slot_for_plan(source_plan)
            source_name = source_plan.get('source_name', slot['name'])
            export_zooms = source_plan.get('export_zooms', [])
            if not export_zooms:
                continue

            sources[source_id] = {
                "format": tile_ext,
                "name": source_name,
                "version": "2.0",
                "type": "raster",
                "minzoom": export_zooms[0],
                "maxzoom": export_zooms[-1],
                "scheme": "xyz",
                "bounds": source_plan['source_bounds'],
                "tiles": [
                    f"smp://maps.v1/s/{source_index}/{{z}}/{{x}}/{{y}}.{tile_ext}"
                ]
            }
            layers.append({
                "id": slot['layer_id'],
                "type": "raster",
                "source": source_id,
                "paint": {
                    "raster-opacity": 1
                }
            })
            source_folders[source_id] = f"s/{source_index}"

        style = {
            "version": 8,
            "name": self._project_title_or_default(),
            "sources": sources,
            "layers": layers,
            "metadata": {
                "smp:bounds": local_bounds,
                "smp:maxzoom": max_zoom,
                "smp:sourceFolders": source_folders
            },
            "center": [center_lon, center_lat],
            "zoom": default_zoom
        }

        return style

    def _get_bounds_wgs84(self, extent):
        """
        Convert the extent to WGS84 bounds

        :param extent: Extent in the project CRS
        :return: Bounds as [west, south, east, north]
        """
        # Get the project CRS
        project_crs = QgsProject.instance().crs()

        # Create a coordinate transform to WGS84
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(project_crs, wgs84, QgsProject.instance())

        # Transform the extent to WGS84
        wgs84_extent = transform.transformBoundingBox(extent)

        # Return as [west, south, east, north]
        return [
            wgs84_extent.xMinimum(),
            wgs84_extent.yMinimum(),
            wgs84_extent.xMaximum(),
            wgs84_extent.yMaximum()
        ]

    @staticmethod
    def _safe_call(obj, method_name):
        method = getattr(obj, method_name, None)
        if not callable(method):
            return None
        try:
            return method()
        except Exception:
            return None

    @staticmethod
    def _source_mtime(source):
        if not source:
            return None
        local_path = source.split('|', 1)[0]
        if os.path.exists(local_path):
            try:
                return os.path.getmtime(local_path)
            except OSError:
                return None
        return None

    def _layer_cache_key(self, layer):
        parts = []
        for name in ('id', 'name'):
            value = self._safe_call(layer, name)
            if value not in (None, ''):
                parts.append(f"{name}={value}")

        source = self._safe_call(layer, 'source') or self._safe_call(layer, 'publicSource')
        if source:
            parts.append(f"source={source}")
            mtime = self._source_mtime(source)
            if mtime is not None:
                parts.append(f"mtime={mtime}")

        renderer = self._safe_call(layer, 'renderer')
        if renderer is not None:
            dump = self._safe_call(renderer, 'dump')
            if dump:
                parts.append(f"renderer={dump}")

        style_manager = self._safe_call(layer, 'styleManager')
        if style_manager is not None:
            current_style = self._safe_call(style_manager, 'currentStyle')
            if current_style not in (None, ''):
                parts.append(f"style={current_style}")

        opacity = self._safe_call(layer, 'opacity')
        if opacity is not None:
            parts.append(f"opacity={opacity}")

        blend_mode = self._safe_call(layer, 'blendMode')
        if blend_mode is not None:
            parts.append(f"blend={blend_mode}")

        return '|'.join(parts) or repr(layer)

    def _project_cache_fingerprint(self, project, layers):
        digest = hashlib.sha256()
        project_crs = self._safe_call(project, 'crs')
        if project_crs is not None:
            authid = self._safe_call(project_crs, 'authid')
            if authid not in (None, ''):
                digest.update(f"project_crs={authid}\n".encode('utf-8', 'ignore'))
        for index, layer in enumerate(layers):
            digest.update(
                f"{index}:{self._layer_cache_key(layer)}\n".encode('utf-8', 'ignore')
            )
        return digest.hexdigest()

    def _visible_layers_in_render_order(self, project):
        root = project.layerTreeRoot()
        visible_nodes = [
            node for node in root.findLayers()
            if node.isVisible() and node.layer() is not None
        ]
        visible_layers = [node.layer() for node in visible_nodes]

        has_custom_order = getattr(root, 'hasCustomLayerOrder', None)
        custom_layer_order = getattr(root, 'customLayerOrder', None)
        if callable(has_custom_order) and has_custom_order() and callable(custom_layer_order):
            visible_ids = {layer.id() for layer in visible_layers}
            ordered_layers = [
                layer for layer in custom_layer_order()
                if layer is not None and layer.id() in visible_ids
            ]
            seen = {layer.id() for layer in ordered_layers}
            ordered_layers.extend(
                layer for layer in visible_layers
                if layer.id() not in seen
            )
            return ordered_layers

        return visible_layers

    def _render_single_tile(self, map_settings_template, zoom, x, y, tiles_dir,
                            tile_format, jpeg_quality, resume,
                            tile_cache=None, fingerprint=None,
                            cancel_event=None, source_index=0):
        """
        Render a single tile and save it to disk.

        :param map_settings_template: Preconfigured QgsMapSettings template
        :param zoom: Zoom level
        :param x: Tile x coordinate
        :param y: Tile y coordinate
        :param tiles_dir: Root directory for tiles
        :param tile_format: Tile image format ('PNG' or 'JPG')
        :param jpeg_quality: JPEG compression quality (1-100)
        :param resume: Skip rendering if tile already exists
        :param tile_cache: Optional TileCache for freshness checks and updates
        :param fingerprint: Current generation fingerprint
        :param cancel_event: Optional threading.Event; if set, skip rendering
        :param source_index: Source index for multi-source archives (default 0)
        :return: True if rendered, False if skipped or cancelled
        """
        # Bail out immediately if cancellation has been signalled
        if cancel_event is not None and cancel_event.is_set():
            return False

        tile_ext = self._tile_extension(tile_format)
        qt_format = self._qt_image_format(tile_format)

        x_dir = os.path.join(tiles_dir, str(source_index), str(zoom), str(x))
        os.makedirs(x_dir, exist_ok=True)
        tile_path = os.path.join(x_dir, f"{y}.{tile_ext}")

        if resume and os.path.exists(tile_path):
            if tile_cache is None or tile_cache.is_fresh(zoom, x, y, fingerprint, source_index=source_index):
                return False

        tile_extent = self._calculate_tile_extent(x, y, zoom)

        # Each thread must use an independent map settings instance.
        ms = QgsMapSettings()
        ms.setDestinationCrs(map_settings_template.destinationCrs())
        ms.setLayers(map_settings_template.layers())
        ms.setOutputSize(map_settings_template.outputSize())
        ms.setExtent(tile_extent)

        tile_size = 256
        if tile_format == self.TILE_FORMAT_JPG:
            img = QImage(tile_size, tile_size, QImage.Format_RGB32)
            img.fill(0xFFFFFFFF)
        else:
            # PNG and WebP both support transparency
            img = QImage(tile_size, tile_size, QImage.Format_ARGB32)
            img.fill(0)

        painter = QPainter(img)
        cancelled = False
        with self._render_lock:
            # QgsMapRendererCustomPainterJob is not thread-safe; only one
            # render job may be active at a time.  The lock also ensures
            # that the Qt event loop can process the job's completion
            # signal without contention from parallel workers.
            job = QgsMapRendererCustomPainterJob(ms, painter)
            job.start()
            job.waitForFinished()
            # Check cancellation *after* the job finishes so we can
            # still abort early for subsequent tiles.
            if ((cancel_event is not None and cancel_event.is_set()) or
                    (self.feedback and self.feedback.isCanceled())):
                if cancel_event is not None:
                    cancel_event.set()
                cancelled = True
        painter.end()

        if cancelled:
            return False

        if tile_format in (self.TILE_FORMAT_JPG, self.TILE_FORMAT_WEBP):
            saved = img.save(tile_path, qt_format, jpeg_quality)
        else:
            saved = img.save(tile_path, qt_format)

        if not saved:
            try:
                os.unlink(tile_path)
            except OSError:
                pass
            raise OSError(f"Failed to save rendered tile: {tile_path}")

        if tile_cache is not None:
            tile_cache.mark(zoom, x, y, fingerprint, defer_save=True, source_index=source_index)

        return True

    def _generate_tiles_from_canvas(self, extent, min_zoom, max_zoom, tiles_dir,
                                    tile_format=None, jpeg_quality=85, resume=False,
                                    max_workers=None, tile_cache=None,
                                    include_world_base_zooms=False,
                                    world_max_zoom=3,
                                    include_region=False,
                                    region_extent=None,
                                    region_min_zoom=None,
                                    region_max_zoom=None,
                                    export_plan=None):
        """
        Generate tiles from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param tiles_dir: Directory to save tiles
        :param tile_format: Tile image format ('PNG' or 'JPG')
        :param jpeg_quality: JPEG compression quality (1-100)
        :param resume: Skip rendering for tiles already present in tiles_dir
        :param max_workers: Number of thread workers. None uses CPU count.
        :param tile_cache: Optional TileCache for incremental invalidation
        """
        if tile_format is None:
            tile_format = self.TILE_FORMAT_PNG

        tile_format = tile_format.upper()

        self.log("Generating tiles from map canvas...")

        # Get the current project
        project = QgsProject.instance()

        # Create map settings for rendering
        map_settings = QgsMapSettings()
        map_settings.setDestinationCrs(project.crs())

        # Add visible layers in layer-tree order (matches what users see in the
        # QGIS layer panel) so that rendering is deterministic across Processing
        # contexts.  findLayers() returns QgsLayerTreeLayer nodes top-to-bottom;
        # the first node is the topmost layer and is rendered on top.
        visible_layers = self._visible_layers_in_render_order(project)
        map_settings.setLayers(visible_layers)

        if export_plan is None:
            export_plan = self._build_export_plan(
                extent,
                min_zoom,
                max_zoom,
                include_world_base_zooms=include_world_base_zooms,
                world_max_zoom=world_max_zoom,
                include_region=include_region,
                region_extent=region_extent,
                region_min_zoom=region_min_zoom,
                region_max_zoom=region_max_zoom,
            )
        total_tiles = export_plan['total_tiles']
        tiles_by_zoom = export_plan['tiles_by_zoom']
        source_plans = export_plan.get('sources', [])
        fingerprint = TileCache.make_fingerprint(
            tile_format,
            jpeg_quality,
            self._project_cache_fingerprint(project, visible_layers),
            self._source_plan_signature(source_plans)
        )

        self.log(f"Total tiles to generate: {total_tiles}")

        # Set the tile size
        tile_size = 256
        map_settings.setOutputSize(QSize(tile_size, tile_size))

        for zoom, min_x, max_x, min_y, max_y, num_tiles, source_index in tiles_by_zoom:
            self.log(
                f"Zoom level {zoom}: {num_tiles} tiles "
                f"({max_x - min_x + 1}x{max_y - min_y + 1})"
            )

        # Log per-source tile counts when multiple sources are present
        source_tile_counts = {}
        for _z, _mx, _Mx, _my, _My, num_tiles, source_index in tiles_by_zoom:
            source_tile_counts[source_index] = source_tile_counts.get(source_index, 0) + num_tiles
        source_labels = self._source_label_map(source_plans)
        if len(source_tile_counts) > 1:
            for si in sorted(source_tile_counts):
                label = source_labels.get(si, f"Source {si}")
                self.log(f"{label} tiles: {source_tile_counts[si]} (source {si})")

        tiles_completed = 0
        last_reported_pct = -1
        progress_lock = threading.Lock()
        cancel_event = threading.Event()
        effective_workers = max_workers if max_workers is not None else os.cpu_count() or 1
        max_pending_futures = max(1, effective_workers * 2)
        wait_timeout_seconds = 0.25
        heartbeat_interval_seconds = 5.0
        last_wait_log = time.monotonic()

        if self.feedback and total_tiles > 0:
            self.feedback.setProgress(0)

        source_labels = self._source_label_map(source_plans)
        _current_source = [None]

        def iter_tile_tasks():
            for zoom, min_x, max_x, min_y, max_y, _, source_index in tiles_by_zoom:
                if _current_source[0] is not None and source_index != _current_source[0]:
                    prev_label = source_labels.get(_current_source[0], f"Source {_current_source[0]}")
                    next_label = source_labels.get(source_index, f"Source {source_index}")
                    self.log(f"Completed {prev_label.lower()} tiles, starting {next_label.lower()} tiles...")
                _current_source[0] = source_index
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        yield (zoom, x, y, source_index)

        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {}
            tile_tasks = iter_tile_tasks()

            def submit_pending():
                while len(futures) < max_pending_futures:
                    if self.feedback and self.feedback.isCanceled():
                        cancel_event.set()
                        return

                    try:
                        zoom, x, y, source_index = next(tile_tasks)
                    except StopIteration:
                        return

                    future = executor.submit(
                        self._render_single_tile,
                        map_settings, zoom, x, y, tiles_dir,
                        tile_format, jpeg_quality, resume,
                        tile_cache, fingerprint, cancel_event,
                        source_index=source_index
                    )
                    futures[future] = (zoom, x, y, source_index)

            submit_pending()

            while futures:
                done, _pending = wait(
                    list(futures.keys()),
                    timeout=wait_timeout_seconds,
                    return_when=FIRST_COMPLETED
                )

                if not done:
                    if self.feedback and self.feedback.isCanceled():
                        cancel_event.set()
                        for pending_future in futures:
                            pending_future.cancel()
                        break

                    now = time.monotonic()
                    if now - last_wait_log >= heartbeat_interval_seconds:
                        sample = list(futures.values())[:3]
                        self.log(
                            f"Waiting on {len(futures)} in-flight tiles; "
                            f"completed {tiles_completed}/{total_tiles}. "
                            f"Sample in-flight tiles: {sample}"
                        )
                        last_wait_log = now
                    continue

                for future in done:
                    _done_tile = futures.pop(future, None)
                    future.result()
                    with progress_lock:
                        tiles_completed += 1
                        if self.feedback and self.feedback.isCanceled():
                            # Signal all running workers to abort and cancel any
                            # futures that have not started yet.
                            cancel_event.set()
                            for pending_future in futures:
                                pending_future.cancel()
                            break
                        if self.feedback and total_tiles > 0:
                            new_pct = int((tiles_completed / total_tiles) * 100)
                            if new_pct != last_reported_pct:
                                self.feedback.setProgress(new_pct)
                                last_reported_pct = new_pct

                        # Log when a source phase finishes
                        if _done_tile is not None:
                            _done_source = _done_tile[3]
                            _remaining_sources = {v[3] for v in futures.values()}
                            if _done_source not in _remaining_sources:
                                _label = source_labels.get(_done_source, f"Source {_done_source}")
                                self.log(f"Completed {_label.lower()} tiles ({tiles_completed}/{total_tiles} total)")

                if cancel_event.is_set():
                    break

                submit_pending()

        if self.feedback and not self.feedback.isCanceled() and total_tiles > 0:
            self.feedback.setProgress(100)

        self.log(f"Generated {tiles_completed} tiles from map canvas")

    def _build_smp_archive(self, style_path, tiles_dir, output_path,
                           tile_paths=None, dedup=False):
        """
        Create SMP archive using style.json and a tiles directory.

        :param style_path: Path to style.json
        :param tiles_dir: Directory containing z/x/y tiles
        :param output_path: Output path for SMP archive
        :param tile_paths: Optional set of relative tile paths (using '/' as
            separator) that belong to the current export.  When provided, only
            tiles in this set are included and internal cache metadata files
            are always excluded.  When None, all tile files are included
            (minus cache metadata).
        :param dedup: When True, use SHA-256 hashing to store duplicate tile
            content only once.  Tiles with identical bytes share the same
            data in the archive, reducing file size for uniform low-zoom tiles.
        """
        self.log(f"Creating SMP archive: {output_path}")
        cancelled = False

        # Collect tile files and their archive names first
        tile_entries = []  # list of (abs_path, archive_name)
        if not (self.feedback and self.feedback.isCanceled()):
            for root, _, files in os.walk(tiles_dir):
                for file in files:
                    if self.feedback and self.feedback.isCanceled():
                        cancelled = True
                        break
                    if file == TileCache.META_FILE:
                        continue
                    fp = os.path.join(root, file)
                    rel = os.path.relpath(fp, tiles_dir).replace(os.sep, '/')
                    if tile_paths is not None and rel not in tile_paths:
                        continue
                    tile_entries.append((fp, 's/' + rel))
                if cancelled:
                    break

        if cancelled:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            return False

        if dedup and tile_entries:
            return self._build_smp_archive_dedup(
                style_path, tile_entries, output_path
            )

        with zipfile.ZipFile(output_path, 'w') as zipf:
            if self.feedback and self.feedback.isCanceled():
                cancelled = True
            else:
                zipf.write(style_path, 'style.json',
                           compress_type=zipfile.ZIP_DEFLATED)
                zipf.writestr('VERSION', '1.0')
            if not cancelled:
                for fp, arcname in tile_entries:
                    if self.feedback and self.feedback.isCanceled():
                        cancelled = True
                        break
                    zipf.write(fp, arcname,
                               compress_type=zipfile.ZIP_STORED)

        if cancelled:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            return False
        return True

    def _build_smp_archive_dedup(self, style_path, tile_entries, output_path):
        """Build an SMP archive with SHA-256 tile deduplication.

        Tiles with identical content are stored only once.  Duplicate
        entries are created in the ZIP central directory that point to
        the same local file header, so all tile paths resolve correctly
        when extracted while saving disk space for duplicate content.

        :param style_path: Path to style.json
        :param tile_entries: List of (abs_path, archive_name) tuples
        :param output_path: Output path for SMP archive
        :return: True on success, False on cancellation.
        """
        cancelled = False

        # Phase 1: Hash all tiles and group by content (streaming — bytes discarded after hash)
        hash_to_meta = {}    # sha256 -> (filepath, first_arcname)
        hash_by_arcname = {}  # arcname -> sha256
        unique_order = []    # ordered list of unique hashes

        for fp, arcname in tile_entries:
            with open(fp, 'rb') as fh:
                data = fh.read()
            content_hash = hashlib.sha256(data).hexdigest()
            hash_by_arcname[arcname] = content_hash
            if content_hash not in hash_to_meta:
                hash_to_meta[content_hash] = (fp, arcname)
                unique_order.append(content_hash)
            if self.feedback and self.feedback.isCanceled():
                cancelled = True
                break

        if cancelled:
            return False  # output file was never created

        num_duplicates = len(tile_entries) - len(unique_order)
        self.log(
            f"Dedup: {len(unique_order)} unique tiles, "
            f"{num_duplicates} duplicates"
        )

        # Phase 2: Build the ZIP file manually for offset control.
        # We write local file headers + data for unique tiles only, then
        # create a central directory with entries for ALL tiles. Duplicate
        # tile CD entries point to the same local header offset but with the
        # duplicate's own arcname. Note: the local header filename will be
        # first_arcname — this is accepted by most ZIP readers for dedup
        # archives (including Android's built-in ZIP handling).
        try:
            with open(output_path, 'wb') as f:
                local_headers = []  # LocalHeaderEntry instances

                # Write style.json
                with open(style_path, 'rb') as sf:
                    style_data = sf.read()
                style_crc = zipfile.crc32(style_data) & 0xFFFFFFFF
                compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
                style_compressed = compressor.compress(style_data) + compressor.flush()
                offset = f.tell()
                local_header = struct.pack(
                    '<IHHHHHIIIHH',
                    0x04034b50, 20, 0,
                    8,  # Compression method: deflate
                    0, 0,
                    style_crc,
                    len(style_compressed),
                    len(style_data),
                    len('style.json'),
                    0
                )
                f.write(local_header)
                f.write(b'style.json')
                f.write(style_compressed)
                local_headers.append(LocalHeaderEntry(
                    offset, 'style.json', style_crc,
                    len(style_compressed), len(style_data)
                ))

                # Write VERSION
                version_data = b'1.0'
                version_crc = zipfile.crc32(version_data) & 0xFFFFFFFF
                offset = f.tell()
                local_header = struct.pack(
                    '<IHHHHHIIIHH',
                    0x04034b50, 20, 0,
                    0,  # ZIP_STORED
                    0, 0,
                    version_crc,
                    len(version_data),
                    len(version_data),
                    len('VERSION'),
                    0
                )
                f.write(local_header)
                f.write(b'VERSION')
                f.write(version_data)
                local_headers.append(LocalHeaderEntry(
                    offset, 'VERSION', version_crc,
                    len(version_data), len(version_data)
                ))

                # Write unique tiles and record offsets
                hash_to_offset = {}  # sha256 -> HashOffsetEntry
                for content_hash in unique_order:
                    if self.feedback and self.feedback.isCanceled():
                        cancelled = True
                        break
                    filepath, first_arcname = hash_to_meta[content_hash]
                    arcname_bytes = first_arcname.encode('utf-8')
                    with open(filepath, 'rb') as fh:
                        data = fh.read()
                    crc = zipfile.crc32(data) & 0xFFFFFFFF
                    offset = f.tell()
                    local_header = struct.pack(
                        '<IHHHHHIIIHH',
                        0x04034b50, 20, 0,
                        0,  # ZIP_STORED
                        0, 0,
                        crc,
                        len(data),
                        len(data),
                        len(arcname_bytes),
                        0
                    )
                    f.write(local_header)
                    f.write(arcname_bytes)
                    f.write(data)
                    hash_to_offset[content_hash] = HashOffsetEntry(
                        offset, crc, len(data), len(data)
                    )

                if not cancelled:
                    # Build central directory entries for ALL tiles (including duplicates)
                    central_dir_entries = []

                    cd_entry = self._make_central_dir_entry(
                        'style.json', local_headers[0], 8  # deflate
                    )
                    central_dir_entries.append(cd_entry)

                    cd_entry = self._make_central_dir_entry(
                        'VERSION', local_headers[1], 0  # stored
                    )
                    central_dir_entries.append(cd_entry)

                    for fp, arcname in tile_entries:
                        content_hash = hash_by_arcname[arcname]
                        offset_info = hash_to_offset[content_hash]
                        cd_entry = self._make_central_dir_entry(arcname, offset_info, 0)
                        central_dir_entries.append(cd_entry)

                    # Guard: standard ZIP format is limited to 65535 entries
                    # (0xFFFF is also the ZIP64 magic marker for entry counts).
                    # style.json + VERSION + tiles, so effective tile limit is 65533.
                    if len(central_dir_entries) >= 65535:
                        raise ValueError(
                            f"Archive has {len(central_dir_entries)} entries, which "
                            f"exceeds the ZIP format limit of 65534. "
                            f"The effective tile limit is 65533 (65535 minus style.json "
                            f"and VERSION). Reduce the zoom range or export extent."
                        )

                    # Write central directory
                    cd_offset = f.tell()
                    for entry in central_dir_entries:
                        f.write(entry)
                        if self.feedback and self.feedback.isCanceled():
                            cancelled = True
                            break

                    if not cancelled:
                        cd_size = f.tell() - cd_offset

                        # Guard: 4GB ZIP32 size limit
                        self._check_zip32_limit(f)

                        # Write end of central directory record
                        eocd = struct.pack(
                            '<IHHHHIIH',
                            0x06054b50,  # EOCD signature
                            0,  # Disk number
                            0,  # Disk with CD
                            len(central_dir_entries),  # Entries on this disk
                            len(central_dir_entries),  # Total entries
                            cd_size,
                            cd_offset,
                            0  # Comment length
                        )
                        f.write(eocd)
        except Exception:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            raise

        if cancelled:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            return False

        return True

    @staticmethod
    def _check_zip32_limit(f):
        """Raise ValueError if the current file position exceeds the 4 GiB ZIP limit.

        Standard ZIP format uses 32-bit unsigned integers for file offsets and sizes.
        If an archive exceeds 4 GiB, those fields silently truncate — producing a
        silently corrupt archive. Call this before writing the EOCD record.

        :param f: Open writable file object (must support tell()).
        :raises ValueError: If f.tell() > 0xFFFFFFFF.
        """
        if f.tell() > 0xFFFFFFFF:
            raise ValueError(
                "Archive size exceeded the 4 GB limit for standard ZIP format. "
                "Use ZIP64 or reduce the export extent and zoom range."
            )

    @staticmethod
    def _make_central_dir_entry(arcname, offset_info, compress_method):
        """Create a central directory entry for a ZIP file.

        :param arcname: Archive entry name
        :param offset_info: A LocalHeaderEntry or HashOffsetEntry named tuple.
        :param compress_method: Compression method (0=stored, 8=deflate)
        :return: Bytes of the central directory entry
        """
        arcname_bytes = arcname.encode('utf-8')

        if isinstance(offset_info, LocalHeaderEntry):
            local_offset = offset_info.offset
            crc = offset_info.crc
            compressed_size = offset_info.compressed_size
            uncompressed_size = offset_info.uncompressed_size
        elif isinstance(offset_info, HashOffsetEntry):
            local_offset = offset_info.offset
            crc = offset_info.crc
            compressed_size = offset_info.compressed_size
            uncompressed_size = offset_info.uncompressed_size
        else:
            raise ValueError(f"Unexpected offset_info type: {type(offset_info)}")

        return struct.pack(
            '<IHHHHHHIIIHHHHHII',
            0x02014b50,  # Central directory file header signature
            20,  # Version made by
            20,  # Version needed to extract
            0,  # General purpose bit flag
            compress_method,
            0, 0,  # Last mod file time and date
            crc,
            compressed_size,
            uncompressed_size,
            len(arcname_bytes),
            0,  # Extra field length
            0,  # File comment length
            0,  # Disk number start
            0,  # Internal file attributes
            0,  # External file attributes
            local_offset
        ) + arcname_bytes


    def _calculate_tile_extent(self, xtile, ytile, zoom):
        """
        Calculate the geographic extent of a specific tile using proper XYZ bounds

        :param xtile: Tile X coordinate
        :param ytile: Tile Y coordinate
        :param zoom: Zoom level
        :return: QgsRectangle in project CRS
        """
        # Get WGS84 bounds for this tile (NW corner)
        north, west = self._num2deg(xtile, ytile, zoom)
        # Get SE corner (next tile's NW corner)
        south, east = self._num2deg(xtile + 1, ytile + 1, zoom)

        # Create rectangle in WGS84
        wgs84_rect = QgsRectangle(west, south, east, north)

        # Transform to project CRS
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        project_crs = QgsProject.instance().crs()
        transform = QgsCoordinateTransform(wgs84, project_crs, QgsProject.instance())

        return transform.transformBoundingBox(wgs84_rect)

    def _calculate_tiles_at_zoom(self, extent, zoom):
        """
        Calculate tile ranges that intersect with the given extent using proper XYZ tiling.
        Handles antimeridian crossing by returning multiple ranges if necessary.

        :param extent: QgsRectangle extent to export
        :param zoom: Zoom level
        :return: List of tuples (min_x, max_x, min_y, max_y) tile coordinates
        """
        # Convert extent to WGS84
        bounds = self._get_bounds_wgs84(extent)
        west, south, east, north = bounds

        # Clamp latitude to Web Mercator limits (±85.0511 degrees)
        north = min(85.0511, max(-85.0511, north))
        south = min(85.0511, max(-85.0511, south))

        n = 1 << zoom  # 2^zoom

        if west > east:
            # Bounding box crosses the antimeridian
            min_x1, min_y = self._deg2num(north, west, zoom)
            max_x1 = n - 1
            min_x2 = 0
            max_x2, max_y = self._deg2num(south, east, zoom)

            min_y = max(0, min(n - 1, min_y))
            max_y = max(0, min(n - 1, max_y))

            min_x1 = max(0, min(n - 1, min_x1))
            max_x1 = max(0, min(n - 1, max_x1))
            min_x2 = max(0, min(n - 1, min_x2))
            max_x2 = max(0, min(n - 1, max_x2))

            return [(min_x1, max_x1, min_y, max_y), (min_x2, max_x2, min_y, max_y)]
        else:
            # Get tile coordinates for corners
            # Note: Y increases from north (0) to south, so northern lat = smaller Y value
            min_x, min_y = self._deg2num(north, west, zoom)
            max_x, max_y = self._deg2num(south, east, zoom)

            # Ensure valid range
            min_x = max(0, min(n - 1, min_x))
            max_x = max(0, min(n - 1, max_x))
            min_y = max(0, min(n - 1, min_y))
            max_y = max(0, min(n - 1, max_y))

            return [(min_x, max_x, min_y, max_y)]
