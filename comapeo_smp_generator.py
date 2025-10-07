# -*- coding: utf-8 -*-

import os
import json
import math
import shutil
import zipfile
import tempfile
from qgis.core import (
    QgsProject,
    QgsMapSettings,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsTask,
    QgsMessageLog,
    Qgis,
    QgsMapRendererCustomPainterJob
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QPainter

class SMPGenerator:
    """
    Class to generate SMP (Styled Map Package) files for CoMapeo
    """

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

    def generate_smp_from_canvas(self, extent, min_zoom, max_zoom, output_path):
        """
        Generate an SMP file from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param output_path: Output path for the SMP file
        :return: Path to the generated SMP file
        """
        self.log(f"Generating SMP file with zoom levels {min_zoom}-{max_zoom}")
        self.log(f"Extent: {extent.asWktPolygon()}")

        # Create a temporary directory for the SMP contents
        temp_dir = tempfile.mkdtemp()
        self.log(f"Using temporary directory: {temp_dir}")

        try:
            # Get the current project
            project = QgsProject.instance()

            # Create the 's' directory for the style
            style_dir = os.path.join(temp_dir, "s")
            os.makedirs(style_dir, exist_ok=True)

            # Generate the style.json file in the root directory
            style = self._create_style_from_canvas(extent, min_zoom, max_zoom)
            style_path = os.path.join(temp_dir, "style.json")
            with open(style_path, 'w') as f:
                json.dump(style, f, indent=4)

            # Generate tiles in the 's/0' directory
            tiles_dir = os.path.join(style_dir, "0")
            os.makedirs(tiles_dir, exist_ok=True)
            self._generate_tiles_from_canvas(extent, min_zoom, max_zoom, tiles_dir)

            # Create the SMP file (zip archive)
            self._create_smp_archive(temp_dir, output_path)

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

    def _create_style_from_canvas(self, extent, min_zoom, max_zoom):
        """
        Create a MapLibre style JSON from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :return: Style JSON object
        """
        # Get the layer bounds in WGS84
        bounds = self._get_bounds_wgs84(extent)

        # Calculate center from bounds
        center_lon = (bounds[0] + bounds[2]) / 2
        center_lat = (bounds[1] + bounds[3]) / 2

        # Calculate appropriate default zoom
        default_zoom = min(max_zoom - 2, 11)

        # Create a basic style following the bash script reference
        source_id = "mbtiles-source"
        style = {
            "version": 8,
            "name": "QGIS MAP",
            "sources": {
                source_id: {
                    "format": "png",
                    "name": "QGIS Map",
                    "version": "2.0",
                    "type": "raster",
                    "minzoom": min_zoom,
                    "maxzoom": max_zoom,
                    "scheme": "xyz",
                    "bounds": bounds,
                    "center": [0, 0, 6],
                    "tiles": [
                        "smp://maps.v1/s/0/{z}/{x}/{y}.png"
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

    def _generate_tiles_from_canvas(self, extent, min_zoom, max_zoom, tiles_dir):
        """
        Generate tiles from the current map canvas

        :param extent: Extent to export
        :param min_zoom: Minimum zoom level
        :param max_zoom: Maximum zoom level
        :param tiles_dir: Directory to save tiles
        """
        self.log("Generating tiles from map canvas...")

        # Get the current project
        project = QgsProject.instance()

        # Create map settings for rendering
        map_settings = QgsMapSettings()
        map_settings.setDestinationCrs(project.crs())

        # Add all visible layers from the project
        layers = project.mapLayers().values()
        visible_layers = [layer for layer in layers if project.layerTreeRoot().findLayer(layer.id()).isVisible()]
        map_settings.setLayers(visible_layers)

        # Calculate total tiles across all zoom levels for progress tracking
        total_tiles = 0
        tiles_by_zoom = []
        for zoom in range(min_zoom, max_zoom + 1):
            min_x, max_x, min_y, max_y = self._calculate_tiles_at_zoom(extent, zoom)
            num_tiles = (max_x - min_x + 1) * (max_y - min_y + 1)
            tiles_by_zoom.append((zoom, min_x, max_x, min_y, max_y, num_tiles))
            total_tiles += num_tiles

        self.log(f"Total tiles to generate: {total_tiles}")

        # Set the tile size
        tile_size = 256
        map_settings.setOutputSize(QSize(tile_size, tile_size))

        # Generate tiles with cumulative progress
        tiles_completed = 0
        for zoom, min_x, max_x, min_y, max_y, num_tiles in tiles_by_zoom:
            zoom_dir = os.path.join(tiles_dir, str(zoom))
            os.makedirs(zoom_dir, exist_ok=True)

            self.log(f"Zoom level {zoom}: {num_tiles} tiles ({max_x - min_x + 1}x{max_y - min_y + 1})")

            # Generate tiles for this zoom level
            for x in range(min_x, max_x + 1):
                x_dir = os.path.join(zoom_dir, str(x))
                os.makedirs(x_dir, exist_ok=True)

                for y in range(min_y, max_y + 1):
                    # Calculate the tile extent using proper XYZ bounds
                    tile_extent = self._calculate_tile_extent(x, y, zoom)
                    map_settings.setExtent(tile_extent)

                    # Render the tile
                    img = QImage(tile_size, tile_size, QImage.Format_ARGB32)
                    img.fill(0)  # Transparent background

                    painter = QPainter(img)
                    job = QgsMapRendererCustomPainterJob(map_settings, painter)
                    job.start()
                    job.waitForFinished()
                    painter.end()

                    # Save the tile
                    tile_path = os.path.join(x_dir, f"{y}.png")
                    img.save(tile_path, "PNG")

                    tiles_completed += 1

                    # Update overall progress
                    if self.feedback:
                        progress = (tiles_completed / total_tiles) * 100
                        self.feedback.setProgress(progress)

        self.log(f"Generated {tiles_completed} tiles from map canvas")

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
        Calculate tile range that intersects with the given extent using proper XYZ tiling

        :param extent: QgsRectangle extent to export
        :param zoom: Zoom level
        :return: Tuple of (min_x, max_x, min_y, max_y) tile coordinates
        """
        # Convert extent to WGS84
        bounds = self._get_bounds_wgs84(extent)
        west, south, east, north = bounds

        # Clamp latitude to Web Mercator limits (±85.0511 degrees)
        north = min(85.0511, max(-85.0511, north))
        south = min(85.0511, max(-85.0511, south))

        # Get tile coordinates for corners
        # Note: Y increases from north (0) to south, so northern lat = smaller Y value
        min_x, min_y = self._deg2num(north, west, zoom)
        max_x, max_y = self._deg2num(south, east, zoom)

        # Ensure valid range
        n = 1 << zoom  # 2^zoom
        min_x = max(0, min(n - 1, min_x))
        max_x = max(0, min(n - 1, max_x))
        min_y = max(0, min(n - 1, min_y))
        max_y = max(0, min(n - 1, max_y))

        return min_x, max_x, min_y, max_y

    def _create_smp_archive(self, source_dir, output_path):
        """
        Create the SMP file (zip archive) from the source directory

        :param source_dir: Source directory containing the SMP contents
        :param output_path: Output path for the SMP file
        """
        self.log(f"Creating SMP archive: {output_path}")

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname)
