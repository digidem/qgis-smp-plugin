# XYZ to SMP Conversion Script

## Overview

`generate_smp_from_xyz.sh` is a bash script that converts XYZ tile directories into Styled Map Package (SMP) files for CoMapeo. This provides an alternative workflow to the QGIS plugin, allowing you to package pre-generated XYZ tiles from any source.

## Prerequisites

- Bash shell (Linux/macOS)
- `zip` utility installed
- XYZ tile directory structure (see below)

## XYZ Tile Directory Structure

The script expects tiles organized in the standard XYZ scheme:

```
project-directory/
├── 0/              # Zoom level 0
│   └── 0/          # X coordinate
│       └── 0.jpg   # Y coordinate tile
├── 1/              # Zoom level 1
│   ├── 0/
│   │   ├── 0.jpg
│   │   └── 1.jpg
│   └── 1/
│       ├── 0.jpg
│       └── 1.jpg
├── 2/              # Zoom level 2
│   └── ...
├── sprites/        # Optional: sprite files
├── fonts/          # Optional: font files
└── metadata/       # Optional: metadata files
```

**Key requirements:**
- Numbered folders (0, 1, 2, ...) represent zoom levels
- Each zoom folder contains X coordinate folders
- Each X folder contains Y coordinate tiles as `.jpg` or `.png` files
- Non-numeric folders (like `sprites/`, `fonts/`) are preserved as additional resources

## Usage

### Running the Script

```bash
cd /path/to/xyz/tiles/
./generate_smp_from_xyz.sh
```

### Interactive Selection

The script will present a menu of available non-numeric folders:

```
Select a folder:
1) sprites
2) fonts
3) metadata
```

Select the number corresponding to the folder you want to include as additional resources (or create a new selection option).

## How It Works

### 1. Folder Detection

```bash
# Lists all non-numeric folders except generated_smp/
options=($(ls -d */ | grep -Ev '^[0-9]+/$' | grep -v 'generated_smp/'))
```

- Scans current directory for folders
- Excludes numeric folders (zoom levels)
- Excludes output directory (`generated_smp/`)

### 2. Max Zoom Detection

```bash
# Finds the highest numbered folder
max_zoom=$(ls -d [0-9]*/ 2>/dev/null | sort -V | tail -1 | tr -d '/')
```

- Identifies all numeric folders (zoom levels)
- Sorts them naturally (0, 1, 2, ..., 10, 11, ...)
- Takes the highest number as `max_zoom`

### 3. Temporary Directory Structure

Creates a temporary directory with SMP structure:

```
temp_<name>_<timestamp>/
├── s/
│   └── 0/              # Source folder ID
│       ├── 0/          # Zoom 0
│       ├── 1/          # Zoom 1
│       ├── ...
│       └── <max>/      # Max zoom
└── style.json          # MapLibre style
```

**Note:** The `s/0/` structure is required by SMP format:
- `s/` = sources directory
- `0/` = encoded source ID (matches `smp:sourceFolders` in metadata)

### 4. Tile Copy Process

```bash
# Copies all zoom level folders to s/0/
for i in $(seq 0 $max_zoom); do
    if [[ -d "$i" ]]; then
        cp -r "$i" "$tmp_dir/s/0/"
    fi
done
```

- Copies all zoom level directories (0 through max_zoom)
- Preserves XYZ directory structure within each zoom level
- Skips missing zoom levels with warning

### 5. Additional Resources Merge

```bash
# Merges selected folder content into s/0/
cp -r "$selected_folder"/* "$tmp_dir/s/0/" 2>/dev/null || true
```

- Copies additional resources (sprites, fonts, etc.) into `s/0/`
- Allows including non-tile assets in the SMP package

### 6. Style.json Generation

Creates a MapLibre GL JS style specification:

```json
{
  "version": 8,
  "name": "javari-<folder>-<year>-z<max_zoom>",
  "sources": {
    "mbtiles-source": {
      "format": "jpg",
      "type": "raster",
      "minzoom": 0,
      "maxzoom": <detected_max_zoom>,
      "scheme": "xyz",
      "bounds": [-73.74, -7.23, -69.38, -4.34],
      "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.jpg"]
    }
  },
  "layers": [
    {
      "id": "background",
      "type": "background",
      "paint": { "background-color": "white" }
    },
    {
      "id": "raster",
      "type": "raster",
      "source": "mbtiles-source",
      "paint": { "raster-opacity": 1 }
    }
  ],
  "metadata": {
    "smp:bounds": [-73.74, -7.23, -69.38, -4.34],
    "smp:maxzoom": <max_zoom>,
    "smp:sourceFolders": {
      "mbtiles-source": "0"
    }
  }
}
```

**Key fields:**
- **`sources.*.tiles`**: Uses `smp://` protocol pointing to `s/0/{z}/{x}/{y}.jpg`
- **`metadata.smp:sourceFolders`**: Maps source ID to folder name (`"0"`)
- **`bounds`**: Geographic extent in WGS84 (currently hardcoded for Javari region)
- **`maxzoom`**: Automatically detected from folder structure

### 7. ZIP Archive Creation

```bash
cd "$tmp_dir"
zip -r "../${output_dir}/${name}.smp" *
```

- Changes into temp directory
- Creates ZIP archive with `.smp` extension
- Includes all contents recursively
- Outputs to `generated_smp/` directory

### 8. Cleanup

```bash
rm -rf "$tmp_dir"
```

Removes temporary directory after successful SMP creation.

## Output

### File Naming Convention

```
javari-<selected_folder>-<year>-z<max_zoom>.smp
```

Example: `javari-sprites-2025-z14.smp`

### Output Location

```
generated_smp/
└── javari-sprites-2025-z14.smp
```

### File Contents

When extracted, the SMP contains:

```
style.json              # MapLibre style specification
s/
└── 0/                  # Source tiles
    ├── 0/              # Zoom levels
    │   └── 0/
    │       └── 0.jpg
    ├── 1/
    ├── ...
    └── <max_zoom>/
```

## Customization

### Changing Geographic Bounds

Edit the `bounds` array in the style.json template (lines 75 and 98):

```bash
"bounds": [<west>, <south>, <east>, <north>]
```

Coordinates must be in WGS84 (EPSG:4326).

### Changing Tile Format

If your tiles are PNG instead of JPG, modify line 68:

```bash
"format": "png",
```

And line 77:

```bash
"tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.png"]
```

### Changing Output Name Pattern

Modify line 25 to customize the naming convention:

```bash
name="custom-prefix-${folder_name}-${current_year}-z${max_zoom}"
```

### Adding Multiple Sources

To support multiple tile sources, you would need to:
1. Create additional folders under `s/` (e.g., `s/1/`, `s/2/`)
2. Add more sources to style.json
3. Update `smp:sourceFolders` metadata mapping

## Differences from QGIS Plugin

| Aspect | XYZ Script | QGIS Plugin |
|--------|------------|-------------|
| **Input** | Pre-generated XYZ tiles | QGIS map canvas |
| **Tile Generation** | Uses existing tiles | Renders tiles from layers |
| **Format** | Fixed (JPG/PNG) | Configurable |
| **Bounds** | Hardcoded (manual edit) | Calculated from extent |
| **Zoom Levels** | Auto-detected | User-specified range |
| **Styling** | Generic raster style | Preserves QGIS styling |
| **Use Case** | Batch processing, external tiles | Interactive map export |

## Troubleshooting

### "No numbered folders found"

Ensure your directory contains folders named `0`, `1`, `2`, etc. representing zoom levels.

### "ZIP creation failed"

- Check disk space
- Verify write permissions
- Ensure `zip` utility is installed: `which zip`

### Missing tiles in output

- Verify XYZ structure: `{z}/{x}/{y}.jpg`
- Check file permissions
- Ensure tiles are in the correct format (JPG/PNG)

### SMP file won't open in CoMapeo

- Verify `style.json` is in the root of the archive
- Check that `s/0/` directory exists
- Ensure tile paths match style.json tile URL pattern
- Validate JSON syntax: `unzip -p file.smp style.json | jq .`

## Example Workflow

```bash
# 1. Prepare XYZ tiles
mkdir my-map
cd my-map

# 2. Organize tiles (manually or via tile generator)
# Structure: 0/0/0.jpg, 1/0/0.jpg, 1/1/0.jpg, etc.

# 3. Run the script
../generate_smp_from_xyz.sh

# 4. Select folder if prompted (or just press Enter)

# 5. Find output
ls generated_smp/
# Output: javari-<name>-2025-z<max>.smp
```

## Integration with QGIS Plugin

While this script provides a manual workflow, the QGIS plugin (`comapeo_smp_generator.py`) automates the entire process:

1. **Tile Generation**: Renders tiles directly from QGIS layers
2. **Bounds Calculation**: Automatically transforms extents to WGS84
3. **Style Creation**: Generates style.json with accurate metadata
4. **Archive Creation**: Packages everything into SMP format

For most use cases, the QGIS plugin is recommended. Use this script when:
- You already have pre-generated XYZ tiles
- You need batch processing of multiple tile sets
- You want to customize the SMP structure beyond QGIS capabilities
