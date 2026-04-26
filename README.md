# CoMapeo SMP Plugin for QGIS

[![Tests](https://github.com/digidem/qgis-smp-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/digidem/qgis-smp-plugin/actions/workflows/test.yml)
[![Lint](https://github.com/digidem/qgis-smp-plugin/actions/workflows/lint.yml/badge.svg)](https://github.com/digidem/qgis-smp-plugin/actions/workflows/lint.yml)
[![Security](https://github.com/digidem/qgis-smp-plugin/actions/workflows/security.yml/badge.svg)](https://github.com/digidem/qgis-smp-plugin/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/digidem/qgis-smp-plugin)](https://github.com/digidem/qgis-smp-plugin/releases)

This QGIS plugin generates Styled Map Package (SMP) files for use with CoMapeo, allowing you to create offline maps from your QGIS projects.

## What is an SMP file?

A Styled Map Package (`.smp`) file is a Zip archive containing a `style.json`
(MapLibre GL style descriptor) and a set of raster XYZ tiles rendered from
your QGIS project.  The plugin generates raster tiles only — it does not
produce vector tiles, glyphs, or sprite assets.

## Installation

### Option 1: Install from QGIS Plugin Repository (Recommended)

1. Open QGIS
2. Go to `Plugins` > `Manage and Install Plugins...`
3. Select the `All` tab
4. Search for "CoMapeo Map Builder"
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
2. Copy or symlink this repository root into your QGIS plugins directory as
   `comapeo_smp`:
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `C:\Users\{username}\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
3. Enable the plugin in QGIS through the Plugin Manager

## Usage

1. Open QGIS and load the layers you want to include in your SMP file
2. Style your layers as desired in the QGIS map canvas
3. Go to `Processing` > `Toolbox` and search for "CoMapeo Map Builder"
4. Select the "Generate SMP Map" tool
5. Configure the following parameters:
   - **Extent**: The local-area geographic extent to include in the SMP file
   - **Minimum zoom level**: The minimum zoom level for the Local detail source (0-24)
   - **Maximum zoom level**: The maximum zoom level for the Local detail source (0-24)
   - **Tile image format**: PNG, JPG, or WebP format for the generated tiles
   - **JPEG/WebP quality**: Compression quality for JPG/WebP tiles (1-100)
   - **Include World overview source**: Optional full-world context rendered below higher-detail sources (enabled by default for backward compatibility)
   - **World maximum zoom**: When World is enabled, generates world tiles from zoom 0 through this zoom
   - **Include Region detail source**: Optional middle-detail source between World and Local
   - **Region extent**: Optional Region extent that must contain the Local extent when Region is enabled
   - **Region minimum zoom level** / **Region maximum zoom level**: Zoom range for the Region detail source
   - **Output SMP file**: The location to save the SMP file
6. Click "Run" to generate the SMP file

When **Include World overview source** is enabled, zoom levels `0..WORLD_MAX_ZOOM`
are exported as full-world tiles on source slot `s/0`. When **Include Region detail
source** is enabled, it occupies source slot `s/1` for its configured zoom range.
If both World and Region are disabled, the selected Local extent uses the legacy
single-source contract on source slot `s/0` with source id `mbtiles-source`.
When either World or Region is enabled, the selected Local extent renders on source
slot `s/2`, above the optional World and Region sources.

The plugin renders visible project layers in QGIS layer-tree order, and uses
custom layer order when that project setting is enabled.

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
# Reliable QGIS-free logic tests (default `make test` path):
make test
# or equivalently:
make test-logic
# or directly:
PYTHONPATH=. python3 test/test_generator.py

# Legacy full test suite (requires QGIS Python env + nosetests;
# exits 0 even when tests fail — do not rely on this in CI):
make test-legacy
```

## License

This plugin is licensed under the GNU General Public License v2.0 or later.

## Credits

Developed by Awana Digital.
