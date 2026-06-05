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
   - **Region minimum zoom level** / **Region maximum zoom level**: Zoom range for the Region detail source; Region requires `WORLD_MAX_ZOOM < REGION_MIN_ZOOM <= REGION_MAX_ZOOM < MIN_ZOOM`
   - **Output SMP file**: The location to save the SMP file
6. Click "Run" to generate the SMP file

### How World / Region / Local Sources Work

The plugin can produce up to three separate tile sources inside a single SMP
file. Each source covers a different zoom range and geographic area, so
CoMapeo can show the right level of detail as the user zooms in and out.

```
 Zoom 0 ────────────────────────────────────────────── Zoom 24
  ┌──────────┐┌───────────────┐┌──────────────────────┐
  │   World   ││    Region     ││       Local          │
  │ Overview  ││    Detail     ││      Detail          │
  │  (s/0)    ││    (s/1)      ││      (s/2)           │
  └──────────┘└───────────────┘└──────────────────────┘
   zoom 0–3     zoom 4–7        zoom 8–14
   (whole       (country/       (your project
    world)       province)       area)
```

**World Overview** (`s/0`) renders the entire globe at very low zoom levels
(0–3 by default). This gives users a recognizable world map when they zoom
out, so they can orient themselves before diving into higher-detail areas.
Enable it with the **Include World overview source** checkbox.

**Region Detail** (`s/1`) is an optional middle layer. It covers a larger
area than your project (for example, a province or watershed) at medium
zoom levels (4–7 by default). This provides a smooth visual transition
between the world overview and your detailed local area. Enable it with the
**Include Region detail source** checkbox, then draw a Region extent that
fully contains your Local extent and set the Region zoom range.

**Local Detail** (`s/2`) is your main project area — the extent you draw in
the dialog — rendered at the highest zoom levels (8–14 by default). This is
always generated. When World and Region are both disabled, Local uses the
legacy single-source slot (`s/0`) for backward compatibility.

The zoom ranges must be strictly ordered so they don't overlap:

```
World max zoom < Region min zoom <= Region max zoom < Local min zoom
```

If your ranges would overlap, the plugin will either automatically adjust
the Local minimum zoom or show a clear error explaining what to change.

**Typical setup for a field project:**

| Source | Extent | Zoom range | Purpose |
|--------|--------|------------|----------|
| World | Entire globe | 0 – 3 | Show continents and oceans when zoomed out |
| Region | Province or territory | 4 – 7 | Show roads and rivers at medium zoom |
| Local | Your project site | 8 – 14 | Show detailed field data at high zoom |

**Simplest setup (World + Local only):**

Leave **Include Region detail source** unchecked. Set **World maximum zoom**
to 3 and **Minimum zoom level** (Local) to 4 or higher. The plugin fills the
gap automatically.

The plugin renders visible project layers in QGIS layer-tree order, and uses
custom layer order when that project setting is enabled.

### Tips for Better Results

- Keep the maximum zoom level reasonable (12-16) to avoid generating too many tiles
- Use a smaller extent for higher zoom levels to reduce processing time and file size
- Make sure all layers are properly styled before generating the SMP file
- Test your SMP file with CoMapeo to ensure it displays correctly

## Migration from earlier versions

This release introduces a fixed source configuration with three slots —
World, Region, and Local — and enforces strict, non-overlapping zoom ranges.
Configurations that were valid in earlier versions may now fail validation.

The required ordering is:

```
world_max_zoom < region_min_zoom <= region_max_zoom < local_min_zoom
```

World is **enabled by default** for backward compatibility, but its zoom
range must not overlap Local (or Region, when Region is enabled). Region
remains optional.

**Example of a config that now fails:** Local **Minimum zoom level** `0`
with World enabled at **World maximum zoom** `3`. Either re-save with
Local **Minimum zoom level** `4` or higher (so Local starts above World's
top zoom), or disable **Include World overview source** if you want Local
to start at zoom `0`. The same rule applies to any Region range that
overlaps World or Local — adjust **Region minimum/maximum zoom level** so
the three ranges remain strictly ordered.

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

This plugin is licensed under the MIT License.

## Credits

Developed by Awana Digital.
