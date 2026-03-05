# QGIS SMP Plugin Tasks Progress Tracker

## Short Term (v0.3.0) - Safety & Optimization
- [x] **Add tile format parameter (PNG/JPG)** (Issue #6)
  - *Details:* Currently hardcoded to PNG. While PNG is great for line maps, raster base maps compress much better as JPG. Supporting JPG could easily reduce the `.smp` file size by 50–80% for imagery.
- [x] **Add JPEG quality setting**
  - *Details:* Allow users to specify the quality of JPEG compression when JPG format is selected.
- [x] **Add tile count estimate before generation**
  - *Details:* Generating XYZ tiles grows exponentially with each zoom level. Warn users if the estimated count is extremely high to prevent QGIS from freezing or taking hours.
- [x] **Add disk space validation**
  - *Details:* Check available disk space before generating large amounts of tiles to prevent filling up the user's hard drive.
- [x] **Add extent size validation/warning**
  - *Details:* Provide a warning if the selected extent and zoom level combination is unreasonably large.

## Medium Term - Modern Mapping Features
- [ ] **Add support for vector tiles (MVT)**
  - *Details:* The MapLibre ecosystem is built around vector tiles. They are much smaller, scale beautifully, and allow the client to change styles dynamically.
- [ ] **Add support for multiple sources in one SMP**
  - *Details:* Allow bundling a satellite raster background with a vector streets/labels layer in a single `.smp` file.
- [ ] **Add preview of tile grid before generation**
  - *Details:* Great for user experience, visualizing what will be generated.
- [ ] **Add resume capability for interrupted generations / Cache Directory**
  - *Details:* Useful when generations take a long time or fail halfway. Allows keeping generated tiles on disk and resuming instead of starting over.
- [ ] **Progress Feedback Smoothing**
  - *Details:* Update the progress bar only when the percentage changes significantly (e.g., batch updates every 50-100 tiles) to reduce UI bottleneck during massive generations.

## Long Term - Performance & Integration
- [ ] **Background processing for large tile generations**
  - *Details:* Generating tiles is CPU-intensive. Running it in the background prevents locking up the QGIS UI.
- [ ] **Parallel tile rendering**
  - *Details:* Take advantage of modern multi-core processors to speed up the process significantly.
- [ ] **Incremental updates (only regenerate changed tiles)**
  - *Details:* Extremely useful for large areas where only a small portion of the data changes frequently.
- [ ] **Direct integration with CoMapeo API**
  - *Details:* Seamless workflow allowing users to push maps directly to the device/server without manually moving files.

## Testing & QA Checklist

### Functional Tests
- [ ] Plugin loads in QGIS without errors
- [ ] Processing algorithm appears in toolbox
- [ ] Can select extent interactively
- [ ] Can set zoom range (0-24)
- [ ] Can specify output path
- [ ] Progress bar shows correctly
- [ ] SMP file is created
- [ ] SMP file structure is correct
- [ ] `style.json` is valid JSON

### Tile Validation Tests
- [ ] Zoom 0 produces 1 tile (or correct subset)
- [ ] Zoom 1 produces correct number of tiles (1-4)
- [ ] Tiles are 256x256 pixels
- [ ] Tiles cover the specified extent
- [ ] No extra tiles generated outside extent

### Error Handling Tests
- [ ] Invalid extent shows error
- [ ] Invalid zoom range shows error
- [ ] Unwritable output path shows error
- [ ] Temp directory cleaned up on success/error
- [ ] Error messages are clear and helpful

### CoMapeo Integration Tests
- [ ] SMP loads in CoMapeo
- [ ] Tiles display at correct locations
- [ ] Tiles align with other map sources
- [ ] No gaps or overlaps between tiles
- [ ] Zoom levels work correctly
