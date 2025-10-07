# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a QGIS plugin that generates Styled Map Package (SMP) files for CoMapeo. An SMP file is a Zip archive containing MapLibre vector styled maps that can be used offline, including style JSON, tiles, glyphs, sprites, and metadata.

## Development Commands

### Testing
```bash
make test
```
Runs the test suite using nosetests with coverage reporting. Tests are located in the `test/` directory.

### Building and Packaging
```bash
# Create a distributable ZIP package
make package VERSION=X.Y.Z

# Deploy to local QGIS plugins directory
make deploy
```

### Code Quality
```bash
# Run pylint
make pylint

# Run PEP8 style checking
make pep8
```

### Translation Management
```bash
# Update translation files
make transup

# Compile translations to .qm files
make transcompile
```

### Development Installation
```bash
# Create symbolic link to QGIS plugins directory
ln -s /path/to/qgis-smp-plugin ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/comapeo_smp
```

## Architecture

### Plugin Entry Point
- **`__init__.py`**: QGIS plugin initialization via `classFactory()` function
- **`comapeo_smp.py`**: Main plugin class `ComapeoMapBuilderPlugin` that registers the processing provider

### Processing Framework Integration
The plugin uses QGIS Processing Framework:

- **`comapeo_smp_provider.py`**: `ComapeoMapBuilderProvider` - Processing provider that registers algorithms
- **`comapeo_smp_algorithm.py`**: `ComapeoMapBuilderAlgorithm` - Processing algorithm interface that handles user parameters (extent, zoom levels, output file)
- **`comapeo_smp_generator.py`**: `SMPGenerator` - Core generation logic

### SMP Generation Flow
1. Algorithm collects parameters from user (extent, min/max zoom, output path)
2. `SMPGenerator.generate_smp_from_canvas()` orchestrates the generation:
   - Creates temporary directory structure (`s/0/` for tiles)
   - Generates MapLibre `style.json` in root with raster source configuration
   - Renders visible layers from map canvas to PNG tiles using `QgsMapRendererCustomPainterJob`
   - Tiles organized as `s/0/{z}/{x}/{y}.png` following XYZ scheme
   - Archives everything into `.smp` ZIP file

### Key Technical Details
- Uses proper Web Mercator tiling scheme (EPSG:3857) following OpenStreetMap slippy map standard
- Implements `_deg2num()` and `_num2deg()` for lat/lon ↔ tile coordinate conversion
- Converts extents to WGS84 bounds for MapLibre compatibility
- Renders each tile at 256x256px from QGIS map canvas
- Style JSON follows MapLibre GL JS v8 specification with `smp://` tile URLs
- Tile rendering respects layer visibility and styling from current QGIS project

### Coordinate System Handling
- Plugin works in project CRS but converts extent to WGS84 for style.json bounds
- Uses `QgsCoordinateTransform` for CRS transformations
- Map rendering maintains project CRS for accuracy
- Tiles align with global XYZ grid using Web Mercator formulas

### XYZ Tiling Implementation
- **Tile Calculation**: Uses `2^zoom` tiles per side (standard XYZ)
- **Tile Bounds**: Each tile has fixed geographic extent based on Web Mercator projection
- **Extent Filtering**: Only generates tiles that intersect with user's specified extent
- **Lat/Lon Conversion**: Based on OpenStreetMap slippy map tilenames formulas:
  ```python
  # Lat/Lon to tile coordinates
  n = 2^zoom
  xtile = int((lon + 180.0) / 360.0 * n)
  ytile = int((1.0 - asinh(tan(lat)) / π) / 2.0 * n)

  # Tile coordinates to Lat/Lon (NW corner)
  lon = xtile / n * 360.0 - 180.0
  lat = atan(sinh(π * (1 - 2 * ytile / n)))
  ```

## Configuration Files

- **`metadata.txt`**: Plugin metadata (name, version, author, dependencies)
- **`pb_tool.cfg`**: Plugin Builder tool configuration
- **`pylintrc`**: Pylint configuration
- **`Makefile`**: Build and development tasks (see QGISDIR variable for plugin path)

## Release Process

GitHub Actions automatically creates releases when changes are pushed to main branch (see `.github/workflows/release.yml`). Update version in `metadata.txt` before pushing.
