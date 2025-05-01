# CoMapeo SMP Plugin for QGIS

This QGIS plugin generates Styled Map Package (SMP) files for use with CoMapeo.

## What is an SMP file?

A Styled Map Package (`.smp`) file is a Zip archive containing all the resources needed to serve a Maplibre vector styled map offline. This includes the style JSON, vector and raster tiles, glyphs (fonts), the sprite image, and the sprite metadata.

## Installation

1. Download the plugin from the QGIS Plugin Repository or install it manually by copying the `comapeo_smp` folder to your QGIS plugins directory.
2. Enable the plugin in QGIS through the Plugin Manager.

## Usage

1. Open QGIS and load the layers you want to include in your SMP file.
2. Style your layers as desired in the QGIS map canvas.
3. Go to `Processing` > `Toolbox` and search for "CoMapeo SMP".
4. Select the "Generate SMP Map" tool.
5. Configure the following parameters:
   - **Extent**: The geographic area to include in the SMP file
   - **Minimum zoom level**: The minimum zoom level to include (0-24)
   - **Maximum zoom level**: The maximum zoom level to include (0-24)
   - **Output SMP file**: The location to save the SMP file
6. Click "Run" to generate the SMP file.

The plugin will use all visible layers from the current map canvas to generate the SMP file, similar to how the MBTiles exporter works.

## Requirements

- QGIS 3.0 or later

## License

This plugin is licensed under the GNU General Public License v2.0 or later.

## Credits

Developed by Awana Digital.
