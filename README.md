# CoMapeo SMP Plugin for QGIS

This QGIS plugin generates Styled Map Package (SMP) files for use with CoMapeo, allowing you to create offline maps from your QGIS projects.

## What is an SMP file?

A Styled Map Package (`.smp`) file is a Zip archive containing all the resources needed to serve a Maplibre vector styled map offline. This includes the style JSON, vector and raster tiles, glyphs (fonts), the sprite image, and the sprite metadata.

## Installation

### Option 1: Install from QGIS Plugin Repository (Recommended)

1. Open QGIS
2. Go to `Plugins` > `Manage and Install Plugins...`
3. Select the `All` tab
4. Search for "CoMapeo SMP"
5. Click `Install Plugin`
6. The plugin will be installed and activated automatically

### Option 2: Manual Installation

1. Download the latest release ZIP file from the [Releases page](https://github.com/digidem/qgis-smp-plugin/releases)
2. Open QGIS
3. Go to `Plugins` > `Manage and Install Plugins...`
4. Select the `Install from ZIP` tab
5. Click `Browse...` and select the downloaded ZIP file
6. Click `Install Plugin`
7. Enable the plugin in the `Installed` tab if it's not already enabled

### Option 3: Development Installation

1. Clone this repository:
   ```
   git clone https://github.com/digidem/qgis-smp-plugin.git
   ```
2. Copy or symlink the `comapeo_smp` folder to your QGIS plugins directory:
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `C:\Users\{username}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
3. Enable the plugin in QGIS through the Plugin Manager

## Usage

1. Open QGIS and load the layers you want to include in your SMP file
2. Style your layers as desired in the QGIS map canvas
3. Go to `Processing` > `Toolbox` and search for "CoMapeo SMP"
4. Select the "Generate SMP Map" tool
5. Configure the following parameters:
   - **Extent**: The geographic area to include in the SMP file
   - **Minimum zoom level**: The minimum zoom level to include (0-24)
   - **Maximum zoom level**: The maximum zoom level to include (0-24)
   - **Output SMP file**: The location to save the SMP file
6. Click "Run" to generate the SMP file

The plugin will use all visible layers from the current map canvas to generate the SMP file, similar to how the MBTiles exporter works.

### Tips for Better Results

- Keep the maximum zoom level reasonable (12-16) to avoid generating too many tiles
- Use a smaller extent for higher zoom levels to reduce processing time and file size
- Make sure all layers are properly styled before generating the SMP file
- Test your SMP file with CoMapeo to ensure it displays correctly

## Requirements

- QGIS 3.0 or later

## Development

### Building the Plugin

To build the plugin for distribution:

```bash
# Create a zip package
make package VERSION=X.Y.Z
```

### Running Tests

```bash
# Run the tests
make test
```

## License

This plugin is licensed under the GNU General Public License v2.0 or later.

## Credits

Developed by Awana Digital.
