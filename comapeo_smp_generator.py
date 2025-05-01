# -*- coding: utf-8 -*-

import os
import json
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

        # Create a basic style following the example schema
        source_id = "mbtiles-source"
        style = {
            "version": 8,
            "name": "QGIS MAP",
            "sources": {
                source_id: {
                    "name": "QGIS Map",
                    "format": "png",
                    "minzoom": min_zoom,
                    "maxzoom": max_zoom,
                    "type": "raster",
                    "description": "Tiles generated from QGIS",
                    "version": "1.0.0",
                    "attribution": "© QGIS",
                    "scheme": "xyz",
                    "bounds": bounds,
                    "center": [0, 0, 8],
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
                    source_id: "0"  # Encoded source ID
                }
            },
            "center": [
                (bounds[0] + bounds[2]) / 2,
                (bounds[1] + bounds[3]) / 2
            ],
            "zoom": min(max_zoom - 2, 14)  # Default zoom level
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
        map_settings.setExtent(extent)

        # Add all visible layers from the project
        layers = project.mapLayers().values()
        visible_layers = [layer for layer in layers if project.layerTreeRoot().findLayer(layer.id()).isVisible()]
        map_settings.setLayers(visible_layers)

        # For each zoom level
        total_tiles = 0
        for zoom in range(min_zoom, max_zoom + 1):
            zoom_dir = os.path.join(tiles_dir, str(zoom))
            os.makedirs(zoom_dir, exist_ok=True)

            # Calculate the number of tiles at this zoom level
            num_tiles_x, num_tiles_y = self._calculate_tiles_at_zoom(extent, zoom)
            total_tiles += num_tiles_x * num_tiles_y

            self.log(f"Zoom level {zoom}: {num_tiles_x}x{num_tiles_y} tiles")

            # Set the tile size
            tile_size = 256
            map_settings.setOutputSize(QSize(tile_size, tile_size))

            # Generate tiles
            for x in range(num_tiles_x):
                x_dir = os.path.join(zoom_dir, str(x))
                os.makedirs(x_dir, exist_ok=True)

                for y in range(num_tiles_y):
                    # Calculate the tile extent
                    tile_extent = self._calculate_tile_extent(extent, zoom, x, y, num_tiles_x, num_tiles_y)
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

                    # Update progress
                    if self.feedback:
                        progress = (x * num_tiles_y + y) / (num_tiles_x * num_tiles_y) * 100
                        self.feedback.setProgress(progress)

        self.log(f"Generated {total_tiles} tiles from map canvas")

    def _calculate_tile_extent(self, full_extent, zoom, x, y, num_tiles_x, num_tiles_y):
        """
        Calculate the extent of a specific tile

        :param full_extent: Full extent to export
        :param zoom: Zoom level (not used in this implementation)
        :param x: Tile X coordinate
        :param y: Tile Y coordinate
        :param num_tiles_x: Number of tiles in X direction
        :param num_tiles_y: Number of tiles in Y direction
        :return: Extent of the tile
        """
        # Calculate the width and height of each tile
        width = full_extent.width() / num_tiles_x
        height = full_extent.height() / num_tiles_y

        # Calculate the coordinates of the tile
        xmin = full_extent.xMinimum() + x * width
        ymin = full_extent.yMinimum() + y * height
        xmax = xmin + width
        ymax = ymin + height

        return QgsRectangle(xmin, ymin, xmax, ymax)

    def _calculate_tiles_at_zoom(self, extent, zoom):
        """
        Calculate the number of tiles needed at a specific zoom level

        :param extent: Extent to export (not used in this implementation)
        :param zoom: Zoom level
        :return: Tuple of (num_tiles_x, num_tiles_y)
        """
        # Calculate the number of tiles based on the zoom level
        # At zoom level 0, the world is covered by a single tile
        # Each zoom level quadruples the number of tiles

        # For a more realistic implementation, we would calculate the actual
        # tile coordinates based on the extent and zoom level using
        # Web Mercator projection formulas

        # For simplicity, we'll use a formula that increases the number of tiles
        # with the zoom level, but keeps it manageable for testing
        tiles_per_side = 2 ** max(0, zoom - 8)
        return max(1, tiles_per_side), max(1, tiles_per_side)

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
