# Testing Plan: Auto-Detection of Zoom-Dependent SMP Sources

**Date:** 2026-04-13
**Status:** Testing Plan — ready for execution
**Parent research:** [research-zoom-sources.md](research-zoom-sources.md)

---

## Purpose

Validate two critical hypotheses before implementation planning:

1. **H1: Cross-fade artifact hypothesis** — MapLibre GL's `raster-fade-duration` (default 300ms) creates visible artifacts at zoom boundaries where rendering changes dramatically within a single raster source.
2. **H2: Clean source transition hypothesis** — Separate raster sources with non-overlapping zoom ranges produce instant, clean transitions at zoom boundaries (no cross-fade).

If H1 is confirmed and H2 is confirmed, auto-detection of zoom-dependent sources provides a real rendering quality improvement, not just metadata/organizational benefits.

---

## Test 1: Single-Source Cross-Fade Artifact

**Goal:** Observe whether MapLibre GL cross-fades tiles at a zoom boundary where rendering changes dramatically within a single source.

### Setup

1. Create a QGIS project with:
   - Layer A: Polygons, visible at scales 1:500,000 and below (i.e. max scale = 1:500,000)
   - Layer B: Points, visible at scales 1:500,000 and above (i.e. min scale = 1:500,000)
   - Both layers covering the same geographic area
   - Use bright, contrasting colors (e.g., red polygons, blue points) so the visual difference is obvious

2. Generate an SMP using the current plugin (single source, no world/region split):
   ```
   Zoom range: 0–14
   Format: PNG
   Extent: small enough to render quickly (e.g., a city-sized area)
   ```

3. The breakpoint at 1:500,000 corresponds to approximately zoom 10 (`log2(559082264 / 500000) ≈ 10.1`). So zoom 9 tiles should show Layer A (polygons) and zoom 10+ tiles should show Layer B (points).

### Execution

1. Serve the SMP using the styled-map-package server:
   ```bash
   cd ../styled-map-package
   npx smp-server ../qgis-smp-plugin/test-output/single-source.smp
   ```

2. Open in MapLibre GL JS (browser):
   - Use a minimal HTML page with MapLibre GL JS
   - Load the SMP's style.json
   - Zoom from level 9 to level 10 slowly

3. **Observe**: Does the transition show a 300ms cross-fade blending the polygon tile with the point tile? Or is it an instant switch?

### Expected Result (H1 confirmed)

- At the zoom 9→10 boundary, a brief cross-fade should be visible — the polygon tile (zoom 9, scaled up) blends with the point tile (zoom 10) for ~300ms.
- This creates a "morphing" artifact where both layers are partially visible simultaneously.

### Alternative: Test with Mapbox GL JS

Since CoMapeo uses `@rnmapbox/maps` (Mapbox SDK), also test with Mapbox GL JS to confirm the same behavior:
```html
<script src='https://api.mapbox.com/mapbox-gl-js/v3.9.4/mapbox-gl.js'></script>
```

---

## Test 2: Multi-Source Clean Transition

**Goal:** Verify that separate sources with non-overlapping zoom ranges produce an instant, clean transition at the zoom boundary.

### Setup

1. Use the same QGIS project from Test 1.

2. Generate two SMPs using the current plugin's world/region split, configured so the breakpoint aligns with the rendering change:
   - World source: zoom 0–9 (shows polygons)
   - Region source: zoom 10–14 (shows points)
   
   Alternatively, manually construct a multi-source SMP by:
   - Generating one SMP for zoom 0–9
   - Generating another SMP for zoom 10–14
   - Combining them into a single SMP with two sources in style.json

3. The style.json should have:
   ```json
   {
     "sources": {
       "source-0": {
         "type": "raster", "minzoom": 0, "maxzoom": 9,
         "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.png"]
       },
       "source-1": {
         "type": "raster", "minzoom": 10, "maxzoom": 14,
         "tiles": ["smp://maps.v1/s/1/{z}/{x}/{y}.png"]
       }
     },
     "layers": [
       {"id": "layer-0", "type": "raster", "source": "source-0", "minzoom": 0, "maxzoom": 9},
       {"id": "layer-1", "type": "raster", "source": "source-1", "minzoom": 10, "maxzoom": 14}
     ]
   }
   ```

### Execution

1. Serve the multi-source SMP using the SMP server.
2. Open in MapLibre GL JS / Mapbox GL JS.
3. Zoom from level 9 to level 10 slowly.

### Expected Result (H2 confirmed)

- At the zoom 9→10 boundary, the transition should be **instant** — no cross-fade.
- Source-0's layer becomes invisible at zoom 10 (layer `maxzoom: 9`).
- Source-1's layer becomes visible at zoom 10 (layer `minzoom: 10`).
- No blending of the two different-looking tiles.

---

## Test 3: CoMapeo On-Device Validation

**Goal:** Confirm multi-source SMPs render correctly on an actual CoMapeo mobile device.

### Prerequisites

- A CoMapeo mobile device or emulator
- The multi-source SMP from Test 2

### Execution

1. Load the multi-source SMP onto the device (via CoMapeo's custom map import).
2. Navigate to the area covered by the SMP.
3. Zoom from level 9 to level 10.
4. Observe the transition.

### Expected Result

- Both sources render correctly at their respective zoom levels.
- The transition at zoom 10 is clean (instant switch).
- No blank tiles at any zoom level within the source ranges.

---

## Test 4: Existing World/Region Split Validation

**Goal:** Confirm the existing 2-source world/region SMP already works correctly in CoMapeo, proving N-source compatibility.

### Setup

1. Generate an SMP with `include_world_base_zooms=True` using the current plugin.
2. This produces a 2-source SMP (world-overview + region-detail).

### Execution

1. Load onto a CoMapeo device.
2. Zoom from world overview zoom range into region detail zoom range.
3. Observe the transition.

### Expected Result

- World source renders at low zooms.
- Region source renders at high zooms.
- Clean transition at the boundary (region layer paints on top of world layer where they overlap).

---

## Test 5: Missing Tile Behavior (No Fallback)

**Goal:** Confirm that missing tiles within a source's minzoom/maxzoom range result in blank display (not fallback to another source or online tiles).

### Setup

1. Manually construct an SMP where source-0 claims `minzoom: 0, maxzoom: 5` but is missing some tiles at zoom 3.

### Execution

1. Serve the SMP.
2. Navigate to the area with missing tiles at zoom 3.

### Expected Result

- Missing tiles show as blank/background color.
- No fallback to online tiles or other sources.
- Confirms that tile completeness within source bounds is critical.

---

## Test Matrix Summary

| Test | Hypothesis | Environment | Pass Criteria |
|------|-----------|-------------|---------------|
| T1: Single-source cross-fade | H1: Cross-fade artifact exists | MapLibre GL JS / Mapbox GL JS | Visible blending at zoom boundary |
| T2: Multi-source clean switch | H2: Instant transition | MapLibre GL JS / Mapbox GL JS | No blending at zoom boundary |
| T3: CoMapeo on-device | Multi-source works on device | CoMapeo mobile | Both sources render, clean transition |
| T4: World/region validation | Existing 2-source works | CoMapeo mobile | World + region both render correctly |
| T5: Missing tile behavior | No fallback for missing tiles | MapLibre GL JS | Blank tiles, no fallback |

---

## Decision Framework

### If T1 passes (cross-fade artifact confirmed):
- Auto-detection provides a **real rendering quality improvement**
- Proceed to implementation planning with high priority

### If T1 fails (no visible cross-fade):
- Auto-detection is **metadata/organizational only**
- Lower priority, but still valuable for authoring clarity
- The `raster-fade-duration` may not apply to raster sources the same way as theory suggests

### If T2 fails (multi-source transition not clean):
- Investigate MapLibre GL source switching behavior more deeply
- May need `raster-fade-duration: 0` on the raster layers
- May need `raster-opacity` expressions for explicit control

### If T3 fails (CoMapeo can't handle multi-source):
- Investigate comapeo-core SMP server version compatibility
- May need updates to comapeo-core before the plugin feature is useful

---

## Quick-Start: Minimal Browser Test

For fastest validation, use this minimal HTML with MapLibre GL JS:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>SMP Source Transition Test</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
  <style>
    body { margin: 0; padding: 0; }
    #map { position: absolute; top: 0; bottom: 0; width: 100%; }
    #info { position: absolute; top: 10px; left: 10px; background: white; padding: 10px; z-index: 1; font-family: monospace; }
  </style>
</head>
<body>
  <div id="info">Zoom: <span id="zoom">--</span></div>
  <div id="map"></div>
  <script>
    // Start SMP server first: npx smp-server path/to/file.smp
    const map = new maplibregl.Map({
      container: 'map',
      style: 'http://localhost:PORT/style.json',  // SMP server URL
      center: [LNG, LAT],  // Center of your test area
      zoom: 9,
    });
    map.on('zoom', () => {
      document.getElementById('zoom').textContent = map.getZoom().toFixed(2);
    });
  </script>
</body>
</html>
```

Replace `PORT`, `LNG`, `LAT` with appropriate values for the SMP server and test area.

---

## Appendix: Key Code References for Test Construction

| What | Where | Notes |
|------|-------|-------|
| SMP server CLI | `styled-map-package/packages/cli/` | `npx smp-server` to serve SMP files |
| SMP Writer (construct multi-source) | `styled-map-package/packages/api/lib/writer.js` | Can programmatically build multi-source SMPs |
| SMP test fixtures | `styled-map-package/packages/api/test/fixtures/` | Existing multi-source test SMPs |
| Plugin world/region split | `comapeo_smp_generator.py:419-477` | Current 2-source generation |
| Plugin style.json generation | `comapeo_smp_generator.py:829-1006` | How style.json is built |
| MapLibre raster-fade-duration | MapLibre Style Spec | Default 300ms, applies to parent/child transitions |
| No fallbackTile in CoMapeo | `comapeo-core/src/fastify-plugins/maps.js:84-87` | Missing tiles → 404 → blank |
| SMP spec completeness requirement | `styled-map-package/spec/1.0/README.md:286` | All tiles within bounds MUST exist |
