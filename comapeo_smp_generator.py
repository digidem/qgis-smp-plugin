# -*- coding: utf-8 -*-

import os
import json
import math
import threading
import shutil
import zipfile
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from qgis.PyQt.QtGui import QImage, QPainter

# Warn if estimated tile count exceeds this threshold
TILE_COUNT_WARNING_THRESHOLD = 5000
# Estimated bytes per tile (PNG ~50 KB, JPG ~15 KB)
BYTES_PER_TILE_PNG = 50 * 1024
BYTES_PER_TILE_JPG = 15 * 1024
# Minimum free disk space to keep (100 MB)
MIN_FREE_SPACE_BYTES = 100 * 1024 * 1024


class TileCache:
    """
    Manages a persistent tile cache with config-based invalidation.

    A JSON sidecar file (`_cache_meta.json`) in `cache_dir` stores a dict
    mapping tile keys ("z/x/y") to the config fingerprint used when that
    tile was last rendered. If the fingerprint for the current run differs,
    the tile is treated as stale and re-rendered.
    """

    META_FILE = '_cache_meta.json'

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self._meta_path = os.path.join(cache_dir, self.META_FILE)
        self._lock = threading.Lock()
        self._meta = self._load()

    def _load(self):
        if os.path.exists(self._meta_path):
            try:
                with open(self._meta_path) as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self):
        """Write metadata atomically: write to a temp file then rename."""
        tmp_path = self._meta_path + '.tmp'
        try:
            with open(tmp_path, 'w') as fh:
                json.dump(self._meta, fh)
            os.replace(tmp_path, self._meta_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def make_fingerprint(tile_format, jpeg_quality):
        """Return a string that identifies the generation config."""
        return f"{tile_format}:{jpeg_quality}"

    def is_fresh(self, zoom, x, y, fingerprint):
        """Return True if the cached tile matches the current fingerprint."""
        key = f"{zoom}/{x}/{y}"
        return self._meta.get(key) == fingerprint

    def mark(self, zoom, x, y, fingerprint):
        """Record that tile (zoom, x, y) was rendered with this fingerprint.

        Thread-safe: acquires the instance lock before mutating shared state.
        """
        key = f"{zoom}/{x}/{y}"
        with self._lock:
            self._meta[key] = fingerprint
            self._save()

    def invalidate(self, zoom, x, y):
        """Remove a tile's fingerprint so it will be re-rendered next run.

        Not used internally; kept for external callers that need to force
        re-rendering of a specific tile (e.g. after source data changes).
        Thread-safe: acquires the instance lock before mutating shared state.
        """
        key = f"{zoom}/{x}/{y}"
        with self._lock:
            self._meta.pop(key, None)
            self._save()




class SMPGenerator:
    """
    Class to generate SMP (Styled Map Package) files for CoMapeo
    """

    TILE_FORMAT_PNG = 'PNG'
    TILE_FORMAT_JPG = 'JPG'

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

    def estimate_tile_count(self, extent, min_zoom, max_zoom):
        """
        Estimate the total number of tiles that will be generated.

        :param extent: QgsRectangle extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: Total estimated tile count
        """
        total = 0
        for zoom in range(min_zoom, max_zoom + 1):
            ranges = self._calculate_tiles_at_zoom(extent, zoom)
            for min_x, max_x, min_y, max_y in ranges:
                total += (max_x - min_x + 1) * (max_y - min_y + 1)
        return total

    def validate_tile_count(self, extent, min_zoom, max_zoom):
        """
        Check estimated tile count and return (count, warning_message).

        :param extent: QgsRectangle extent
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: Tuple of (tile_count, warning_message_or_None)
        """
        count = self.estimate_tile_count(extent, min_zoom, max_zoom)
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
        if tile_format == self.TILE_FORMAT_JPG:
            bytes_per_tile = BYTES_PER_TILE_JPG
        else:
            bytes_per_tile = BYTES_PER_TILE_PNG

        estimated_bytes = tile_count * bytes_per_tile
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

    def validate_extent_size(self, extent, min_zoom, max_zoom):
        """
        Warn if the extent+zoom combination is unreasonably large.

        :param extent: QgsRectangle extent in project CRS
        :param min_zoom: Minimum zoom level
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

    def get_tile_grid_rects(self, extent, min_zoom, max_zoom):
        """
        Return the WGS84 bounding rectangles of all tiles that would be generated.

        :param extent: QgsRectangle extent in project CRS
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: list of dicts:
                 [{"zoom": z, "x": x, "y": y,
                   "west": w, "south": s, "east": e, "north": n}, ...]
        """
        rects = []
        for zoom in range(min_zoom, max_zoom + 1):
            ranges = self._calculate_tiles_at_zoom(extent, zoom)
            for min_x, max_x, min_y, max_y in ranges:
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
                            "north": north
                        })
        return rects

    def generate_smp_from_canvas(self, extent, min_zoom, max_zoom, output_path,
                                 tile_format=None, jpeg_quality=85, cache_dir=None,
                                 max_workers=None):
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

        tile_format = tile_format.upper()
        if tile_format not in (self.TILE_FORMAT_PNG, self.TILE_FORMAT_JPG):
            raise ValueError(f"Unsupported tile format: {tile_format}. Use 'PNG' or 'JPG'.")

        jpeg_quality = max(1, min(100, int(jpeg_quality)))

        self.log(f"Generating SMP file with zoom levels {min_zoom}-{max_zoom}")
        self.log(f"Extent: {extent.asWktPolygon()}")
        self.log(f"Tile format: {tile_format}" +
                 (f", JPEG quality: {jpeg_quality}" if tile_format == self.TILE_FORMAT_JPG else ""))

        # --- Pre-generation validations ---
        tile_count, count_warning = self.validate_tile_count(extent, min_zoom, max_zoom)
        self.log(f"Estimated tile count: {tile_count:,}")
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
            style = self._create_style_from_canvas(extent, min_zoom, max_zoom, tile_format)
            style_path = os.path.join(temp_dir, "style.json")
            with open(style_path, 'w') as f:
                json.dump(style, f, indent=4)

            if cache_dir is not None:
                os.makedirs(cache_dir, exist_ok=True)
                tiles_dir = cache_dir
                tile_cache = TileCache(cache_dir)
            else:
                tiles_dir = os.path.join(style_dir, "0")
                os.makedirs(tiles_dir, exist_ok=True)
                tile_cache = None
            self._generate_tiles_from_canvas(
                extent, min_zoom, max_zoom, tiles_dir,
                tile_format=tile_format, jpeg_quality=jpeg_quality,
                resume=(cache_dir is not None),
                max_workers=max_workers,
                tile_cache=tile_cache
            )

            # Build the set of tile paths that belong to *this* export so that
            # stale tiles from previous runs are excluded from the archive.
            # Only needed when cache_dir is used (otherwise tiles_dir is fresh).
            tile_paths = None
            if cache_dir is not None:
                tile_ext = 'jpg' if tile_format == self.TILE_FORMAT_JPG else 'png'
                tile_paths = set()
                for zoom in range(min_zoom, max_zoom + 1):
                    ranges = self._calculate_tiles_at_zoom(extent, zoom)
                    for min_x, max_x, min_y, max_y in ranges:
                        for x in range(min_x, max_x + 1):
                            for y in range(min_y, max_y + 1):
                                tile_paths.add(
                                    f"{zoom}/{x}/{y}.{tile_ext}"
                                )

            # Create the SMP file (zip archive)
            self._build_smp_archive(
                style_path=os.path.join(temp_dir, "style.json"),
                tiles_dir=tiles_dir,
                output_path=output_path,
                tile_paths=tile_paths
            )

            self.log(f"SMP file generated successfully: {output_path}")
            return output_path

        except Exception as e:
            self.log(f"Error generating SMP file: {str(e)}", Qgis.Critical)
            raise
        finally:
            # Always clean up temporary directory
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                self.log(f"Cleaned up temporary directory: {temp_dir}")

    def _create_style_from_canvas(self, extent, min_zoom, max_zoom, tile_format=None):
        """
        Create a MapLibre style JSON from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param tile_format: Tile image format ('PNG' or 'JPG')
        :return: Style JSON object
        """
        if tile_format is None:
            tile_format = self.TILE_FORMAT_PNG

        tile_ext = 'jpg' if tile_format.upper() == self.TILE_FORMAT_JPG else 'png'

        # Get the layer bounds in WGS84
        bounds = self._get_bounds_wgs84(extent)

        # Calculate center from bounds
        center_lon = (bounds[0] + bounds[2]) / 2
        center_lat = (bounds[1] + bounds[3]) / 2

        # Calculate appropriate default zoom (must be >= 0)
        default_zoom = max(0, min(max_zoom - 2, 11))

        # Create a basic style following the bash script reference
        source_id = "mbtiles-source"
        style = {
            "version": 8,
            "name": "QGIS MAP",
            "sources": {
                source_id: {
                    "format": tile_ext,
                    "name": "QGIS Map",
                    "version": "2.0",
                    "type": "raster",
                    "minzoom": min_zoom,
                    "maxzoom": max_zoom,
                    "scheme": "xyz",
                    "bounds": bounds,
                    "center": [0, 0, 6],
                    "tiles": [
                        f"smp://maps.v1/s/0/{{z}}/{{x}}/{{y}}.{tile_ext}"
                    ]
                }
            },
            "layers": [
                {
                    "id": "background",
                    "type": "background",
                    "paint": {
                        "background-color": "white"
                    }
                },
                {
                    "id": "raster",
                    "type": "raster",
                    "source": source_id,
                    "paint": {
                        "raster-opacity": 1
                    }
                }
            ],
            "metadata": {
                "smp:bounds": bounds,
                "smp:maxzoom": max_zoom,
                "smp:sourceFolders": {
                    source_id: "0"
                }
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

    def _render_single_tile(self, map_settings_template, zoom, x, y, tiles_dir,
                            tile_format, jpeg_quality, resume,
                            tile_cache=None, fingerprint=None):
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
        :return: True if rendered, False if skipped
        """
        tile_ext = 'jpg' if tile_format == self.TILE_FORMAT_JPG else 'png'
        qt_format = 'JPEG' if tile_format == self.TILE_FORMAT_JPG else 'PNG'

        x_dir = os.path.join(tiles_dir, str(zoom), str(x))
        os.makedirs(x_dir, exist_ok=True)
        tile_path = os.path.join(x_dir, f"{y}.{tile_ext}")

        if resume and os.path.exists(tile_path):
            if tile_cache is None or tile_cache.is_fresh(zoom, x, y, fingerprint):
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
            img = QImage(tile_size, tile_size, QImage.Format_ARGB32)
            img.fill(0)

        painter = QPainter(img)
        job = QgsMapRendererCustomPainterJob(ms, painter)
        job.start()
        job.waitForFinished()
        painter.end()

        if tile_format == self.TILE_FORMAT_JPG:
            img.save(tile_path, qt_format, jpeg_quality)
        else:
            img.save(tile_path, qt_format)

        if tile_cache is not None:
            tile_cache.mark(zoom, x, y, fingerprint)

        return True

    def _generate_tiles_from_canvas(self, extent, min_zoom, max_zoom, tiles_dir,
                                    tile_format=None, jpeg_quality=85, resume=False,
                                    max_workers=None, tile_cache=None):
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
        fingerprint = TileCache.make_fingerprint(tile_format, jpeg_quality)

        self.log("Generating tiles from map canvas...")

        # Get the current project
        project = QgsProject.instance()

        # Create map settings for rendering
        map_settings = QgsMapSettings()
        map_settings.setDestinationCrs(project.crs())

        # Add all visible layers from the project
        layers = project.mapLayers().values()
        visible_layers = [
            layer for layer in layers
            if project.layerTreeRoot().findLayer(layer.id()).isVisible()
        ]
        map_settings.setLayers(visible_layers)

        # Calculate total tiles across all zoom levels for progress tracking
        total_tiles = 0
        tiles_by_zoom = []
        for zoom in range(min_zoom, max_zoom + 1):
            ranges = self._calculate_tiles_at_zoom(extent, zoom)
            for min_x, max_x, min_y, max_y in ranges:
                num_tiles = (max_x - min_x + 1) * (max_y - min_y + 1)
                tiles_by_zoom.append((zoom, min_x, max_x, min_y, max_y, num_tiles))
                total_tiles += num_tiles

        self.log(f"Total tiles to generate: {total_tiles}")

        # Set the tile size
        tile_size = 256
        map_settings.setOutputSize(QSize(tile_size, tile_size))

        for zoom, min_x, max_x, min_y, max_y, num_tiles in tiles_by_zoom:
            self.log(
                f"Zoom level {zoom}: {num_tiles} tiles "
                f"({max_x - min_x + 1}x{max_y - min_y + 1})"
            )

        tile_tasks = [
            (zoom, x, y)
            for zoom, min_x, max_x, min_y, max_y, _ in tiles_by_zoom
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1)
        ]

        tiles_completed = 0
        last_reported_pct = -1
        lock = threading.Lock()
        effective_workers = max_workers if max_workers is not None else os.cpu_count() or 1

        if self.feedback and total_tiles > 0:
            self.feedback.setProgress(0)

        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {
                executor.submit(
                    self._render_single_tile,
                    map_settings, zoom, x, y, tiles_dir,
                    tile_format, jpeg_quality, resume,
                    tile_cache, fingerprint
                ): (zoom, x, y)
                for zoom, x, y in tile_tasks
            }

            for future in as_completed(futures):
                future.result()
                with lock:
                    tiles_completed += 1
                    if self.feedback and self.feedback.isCanceled():
                        break
                    if self.feedback and total_tiles > 0:
                        new_pct = int((tiles_completed / total_tiles) * 100)
                        if new_pct != last_reported_pct:
                            self.feedback.setProgress(new_pct)
                            last_reported_pct = new_pct

        if self.feedback and not self.feedback.isCanceled() and total_tiles > 0:
            self.feedback.setProgress(100)

        self.log(f"Generated {tiles_completed} tiles from map canvas")

    def _build_smp_archive(self, style_path, tiles_dir, output_path,
                           tile_paths=None):
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
        """
        self.log(f"Creating SMP archive: {output_path}")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(style_path, 'style.json')
            for root, _, files in os.walk(tiles_dir):
                for file in files:
                    # Never package internal cache metadata
                    if file == TileCache.META_FILE:
                        continue
                    fp = os.path.join(root, file)
                    rel = os.path.relpath(fp, tiles_dir).replace(os.sep, '/')
                    # When a tile manifest is provided, skip stale tiles
                    if tile_paths is not None and rel not in tile_paths:
                        continue
                    zipf.write(fp, 's/0/' + rel)

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
