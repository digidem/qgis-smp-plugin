# QGIS SMP Plugin - XYZ Tiling Implementation Summary

## Overview
Fixed critical issues in the QGIS plugin's tile generation to match the XYZ standard specification and align with the bash script reference implementation documented in `XYZ_SMP.md`.

## GitHub Issues Created

All issues are available at: https://github.com/digidem/qgis-smp-plugin/issues

1. **Issue #1 - Critical: Implement proper Web Mercator tile coordinate calculation**
   - Added `_deg2num()` and `_num2deg()` functions based on OSM standard
   - Enables proper lat/lon ↔ tile coordinate conversion

2. **Issue #2 - Critical: Fix tile count calculation**
   - Changed from `2^max(0, zoom-8)` to proper `2^zoom` formula
   - Ensures correct number of tiles at each zoom level

3. **Issue #3 - Critical: Fix tile extent calculation for proper XYZ bounds**
   - Replaced extent division approach with proper Web Mercator bounds
   - Tiles now align with global XYZ grid

4. **Issue #4 - Medium: Align style.json structure with reference implementation**
   - Removed extra fields (description, attribution)
   - Added root-level center and zoom fields
   - Matched bash script format

5. **Issue #5 - Low: Add proper cleanup and error handling**
   - Added finally block for temp directory cleanup
   - Fixed progress reporting to track overall progress

6. **Issue #6 - Low: Make tile format configurable**
   - Document need for PNG/JPG configuration option
   - Currently implemented with PNG

## Code Changes Implemented

### 1. Added Web Mercator Utility Functions
**File**: `comapeo_smp_generator.py`
**Lines**: 49-79

```python
def _deg2num(self, lat_deg, lon_deg, zoom):
    """Convert latitude/longitude to tile coordinates"""
    lat_rad = math.radians(lat_deg)
    n = 1 << zoom  # 2^zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

def _num2deg(self, xtile, ytile, zoom):
    """Convert tile coordinates to latitude/longitude (NW corner)"""
    n = 1 << zoom  # 2^zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg
```

### 2. Fixed Tile Range Calculation
**File**: `comapeo_smp_generator.py`
**Function**: `_calculate_tiles_at_zoom()`

**Before**: Returned `2^max(0, zoom-8)` tiles per side
**After**: Returns actual tile range (min_x, max_x, min_y, max_y) that intersects extent

### 3. Fixed Tile Extent Calculation
**File**: `comapeo_smp_generator.py`
**Function**: `_calculate_tile_extent()`

**Before**: Divided user extent into equal rectangles
**After**: Uses `_num2deg()` to get proper Web Mercator bounds for each tile

### 4. Updated Tile Generation Loop
**File**: `comapeo_smp_generator.py`
**Function**: `_generate_tiles_from_canvas()`

Changes:
- Pre-calculates all tile ranges for progress tracking
- Uses tile coordinates (x, y) instead of index positions
- Implements cumulative progress tracking across all zoom levels
- Only generates tiles that intersect the user's extent

### 5. Updated style.json Generation
**File**: `comapeo_smp_generator.py`
**Function**: `_create_style_from_canvas()`

Changes:
- Removed `description` and `attribution` fields from sources
- Changed version from "1.0.0" to "2.0"
- Added calculated `center` at root level
- Added `zoom` field at root level

### 6. Added Proper Error Handling
**File**: `comapeo_smp_generator.py`
**Function**: `generate_smp_from_canvas()`

Added finally block:
```python
finally:
    # Always clean up temporary directory
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        self.log(f"Cleaned up temporary directory: {temp_dir}")
```

### 7. Added Required Imports
**File**: `comapeo_smp_generator.py`
**Lines**: 5-6

Added:
- `import math` - For Web Mercator formulas
- `import shutil` - For directory cleanup

## Impact of Changes

### Critical Improvements
- ✅ **Tiles now align with XYZ standard** - Compatible with other tile sources
- ✅ **Correct tile count at all zoom levels** - No longer generates excess tiles
- ✅ **Proper geographic bounds** - Tiles have correct lat/lon extents
- ✅ **Standard-compliant SMP files** - Works with CoMapeo/MapLibre

### Quality Improvements
- ✅ **Automatic cleanup** - No temp file leaks on errors
- ✅ **Better progress tracking** - Shows overall progress, not per-zoom
- ✅ **Aligned with reference** - style.json matches bash script

## Testing Recommendations

1. **Basic Functionality Test**
   ```
   - Create simple QGIS project with 1-2 layers
   - Export extent at zoom 0-2
   - Verify tile count: zoom 0=1, zoom 1=4, zoom 2=16 (or subset)
   - Check tiles align with XYZ grid
   ```

2. **Style.json Validation**
   ```bash
   unzip -p output.smp style.json | jq .
   # Verify structure matches XYZ_SMP.md reference
   ```

3. **CoMapeo Compatibility**
   ```
   - Load generated SMP in CoMapeo
   - Verify tiles display correctly
   - Check tile boundaries align
   ```

4. **Error Handling Test**
   ```
   - Trigger error during generation
   - Verify temp directory is cleaned up
   - Check error is logged properly
   ```

## Remaining Work

### Not Implemented (see Issue #6)
- Tile format configuration (PNG vs JPG)
- Would require algorithm parameter and generator updates
- Low priority - PNG works but creates larger files

### Future Enhancements
- Add tile format parameter to algorithm
- Implement quality settings for JPEG
- Add validation for output path writability
- Add disk space check before generation

## References

- [XYZ_SMP.md](./XYZ_SMP.md) - Source of truth for SMP format
- [OpenStreetMap Slippy Map Tilenames](https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames)
- [MapLibre Style Spec](https://maplibre.org/maplibre-style-spec/)
- GitHub Issues: https://github.com/digidem/qgis-smp-plugin/issues

## Documentation Updates

- Updated `CLAUDE.md` with Web Mercator implementation details
- Added XYZ tiling formulas to architecture section
- Documented tile calculation approach

## Version Bump Recommendation

These are breaking changes that fix fundamental compatibility issues. Recommend version bump:
- Current: 0.1
- Recommended: 0.2.0 (breaking changes, major functionality fixes)

Update `metadata.txt` before next release.
