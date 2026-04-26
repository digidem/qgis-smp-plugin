# Auto-Detection of Zoom-Dependent SMP Sources — Feasibility Research

**Date:** 2026-04-12 (updated 2026-04-13)
**Status:** Research Complete — testing plan pending
**Testing plan:** [research-zoom-sources-testing.md](research-zoom-sources-testing.md)

---

## Question

Can the QGIS SMP plugin automatically detect when QGIS layers use different rendering options (scale-dependent visibility, label rules, style changes) at different zoom levels, and automatically create separate SMP sources for each zoom range that has a distinct visual appearance?

---

## 1. Current Plugin Architecture: Flat Single-Source Rendering

The plugin currently renders **all visible layers at all zoom levels identically**. In `_render_single_tile` (`comapeo_smp_generator.py:1129-1220`), each tile is rendered with the same `map_settings_template` regardless of zoom level:

```python
ms = QgsMapSettings()
ms.setDestinationCrs(map_settings_template.destinationCrs())
ms.setLayers(map_settings_template.layers())  # same layers at every zoom
ms.setOutputSize(map_settings_template.outputSize())
ms.setExtent(tile_extent)
```

There is **no per-zoom configuration** of layers, scale denominators, or rendering options. QGIS's own scale-dependent layer visibility and rule-based rendering **do** get respected because `setExtent()` implicitly sets the map scale (a 256px tile at zoom 10 covers a much smaller geographic area than at zoom 4), but the plugin does not introspect or control this.

## 2. The Only Multi-Source Mechanism Today: World vs. Region

The existing multi-source support (`comapeo_smp_generator.py:419-477`) is strictly geographic, not style-based:

- **Source 0** (`world-overview`): Full-world extent at low zooms (0–`world_max_zoom`)
- **Source 1** (`region-detail`): User-selected extent at higher zooms (`min_zoom`–`max_zoom`)

This split is purely about **extent**, not about rendering differences. Both sources render from the same QGIS canvas with the same layers and styles.

## 3. What Would Need to Change

### A. Detection: Determining When Visual Output Differs Between Zoom Levels

QGIS provides APIs to introspect scale-dependent behavior:

- **Layer scale visibility**: `QgsMapLayer.hasScaleBasedVisibility()`, `minimumScale()`, `maximumScale()` — tells you if a layer appears/disappears at certain scales.
- **Rule-based rendering**: `QgsRuleBasedRenderer` has child rules with `scaleMinDenom()` / `scaleMaxDenom()` — rules that activate at different scales.
- **Label scale ranges**: `QgsPalLayerSettings.scaleVisibility`, `minimumScale`, `maximumScale`.
- **Zoom-to-scale conversion**: Each zoom level maps to a known map scale denominator (e.g., zoom 0 ≈ 1:559M, zoom 10 ≈ 1:534K, zoom 14 ≈ 1:35K). The plugin already computes tile extents per zoom, so deriving the scale is straightforward.

**This detection is technically feasible.** You could:

1. For each zoom level, compute the scale denominator for a 256px tile.
2. For each visible layer, check if its rendering differs at that scale (layer visibility changes, rule-based renderer activates different rules, label settings change).
3. Group consecutive zoom levels that produce the same "effective rendering configuration" into source ranges.

### B. Generation: Rendering Different Sources Per Zoom Group

**The easy part** — The plugin already supports multi-source archives with per-source tile directories (`s/0/`, `s/1/`, etc.) and per-source entries in `style.json`. The `_build_export_plan` and `_create_style_from_canvas` methods already handle 2-source plans.

**The hard part** — Currently, the plugin renders **raster tiles** from the QGIS canvas. Each tile is a flat image. The SMP format stores raster tiles, not vector data. So:

- If Layer A is visible at zoom 0–8 and Layer B at zoom 8–14, the plugin would need to render **different layer compositions** at different zoom levels and store them in separate source directories.
- This means `_render_single_tile` would need zoom-aware layer selection: `ms.setLayers(zoom_appropriate_layers)` instead of `map_settings_template.layers()`.
- The `_build_export_plan` would need to create N sources (one per zoom-group) instead of just 1 or 2.
- The `style.json` would need N source entries with non-overlapping `minzoom`/`maxzoom` ranges, plus N corresponding raster layers.

## 4. QGIS API Availability (Verified)

All necessary APIs exist in QGIS Python bindings:

### Layer-level scale visibility
- `QgsMapLayer.hasScaleBasedVisibility()` → bool
- `QgsMapLayer.minimumScale()` → double (max scale denominator, i.e. "most zoomed out")
- `QgsMapLayer.maximumScale()` → double (min scale denominator, i.e. "most zoomed in")

### Rule-based renderer scale ranges
- `QgsRuleBasedRenderer::Rule.dependsOnScale()` → bool
- `QgsRuleBasedRenderer::Rule.maximumScale()` → double
- `QgsRuleBasedRenderer::Rule.minimumScale()` → double
- `QgsRuleBasedRenderer::Rule.isScaleOK(double scale)` → bool
- `QgsRuleBasedRenderer::Rule.descendants()` → RuleList (for recursive tree traversal)

### Label scale visibility
- `QgsPalLayerSettings.scaleVisibility` → bool
- `QgsPalLayerSettings.minimumScale` → double
- `QgsPalLayerSettings.maximumScale` → double
- `QgsRuleBasedLabeling::Rule.dependsOnScale()` → bool
- `QgsRuleBasedLabeling::Rule.maximumScale()` → double
- `QgsRuleBasedLabeling::Rule.minimumScale()` → double
- `QgsRuleBasedLabeling::Rule.settings()` → QgsPalLayerSettings*

### Zoom-to-scale conversion
- Standard Web Mercator formula: `scale = 559082264.028 / 2^zoom` at 256px/96 DPI
- Deterministic and easy to compute

## 5. Exact Code Locations That Need Modification

| Component | Location | Change Needed |
|-----------|----------|---------------|
| Detection logic | New method (e.g. `_detect_rendering_breakpoints`) | Walk all visible layers, collect scale boundaries from layer visibility, renderer rules, and label settings; compute zoom-level groups |
| Export plan builder | `comapeo_smp_generator.py:419-477` (`_build_export_plan`) | Generalize from 2 fixed sources to N dynamic sources based on detected rendering groups |
| Style generator | `comapeo_smp_generator.py:829-1006` (`_create_style_from_canvas`) | Generalize from 2-source hardcoded structure to N-source loop |
| Tile renderer | `comapeo_smp_generator.py:1169-1170` (`_render_single_tile`) | Optionally pass per-source layer list so `ms.setLayers()` uses zoom-appropriate layers |
| Cache fingerprint | `comapeo_smp_generator.py:1091-1102` (`_project_cache_fingerprint`) | Include per-zoom-group rendering config in fingerprint |
| Algorithm UI | `comapeo_smp_algorithm.py` | Add checkbox/option for "auto-detect zoom sources" |

## 6. Detection Algorithm Sketch

```python
def _detect_rendering_breakpoints(self, layers, min_zoom, max_zoom):
    """Return list of (zoom_min, zoom_max, layer_config_signature) tuples."""
    
    # Step 1: Collect all scale boundaries from all layers
    scale_breakpoints = set()
    for layer in layers:
        if layer.hasScaleBasedVisibility():
            scale_breakpoints.add(layer.minimumScale())
            scale_breakpoints.add(layer.maximumScale())
        
        renderer = layer.renderer()
        if isinstance(renderer, QgsRuleBasedRenderer):
            for rule in renderer.rootRule().descendants():
                if rule.dependsOnScale():
                    scale_breakpoints.add(rule.maximumScale())
                    scale_breakpoints.add(rule.minimumScale())
        
        if hasattr(layer, 'labeling') and layer.labeling():
            labeling = layer.labeling()
            if isinstance(labeling, QgsVectorLayerSimpleLabeling):
                settings = labeling.settings()
                if settings.scaleVisibility:
                    scale_breakpoints.add(settings.minimumScale)
                    scale_breakpoints.add(settings.maximumScale)
            elif isinstance(labeling, QgsRuleBasedLabeling):
                root = labeling.rootRule()
                for rule in root.descendants():
                    if rule.dependsOnScale():
                        scale_breakpoints.add(rule.maximumScale())
                        scale_breakpoints.add(rule.minimumScale())
    
    # Step 2: Convert scale breakpoints to zoom levels
    zoom_breakpoints = set()
    for scale in scale_breakpoints:
        if scale > 0:
            zoom = max(0, int(math.log2(559082264.028 / scale)))
            zoom_breakpoints.add(zoom)
    
    # Step 3: For each zoom range, compute a "rendering signature"
    all_zooms = sorted(set(range(min_zoom, max_zoom + 1)) | zoom_breakpoints)
    groups = []
    # ... group consecutive zooms with identical signatures
```

## 7. Feasibility Assessment

| Aspect | Feasibility | Notes |
|--------|-------------|-------|
| Detect layer scale visibility | **HIGH** | Direct QGIS API |
| Detect rule-based renderer changes | **HIGH** | Tree walk needed but API exists |
| Detect label scale changes (simple) | **HIGH** | Direct attributes |
| Detect label scale changes (rule-based) | **MEDIUM-HIGH** | Same pattern as renderer rules |
| Generate N sources in export plan | **MEDIUM** | Refactor `_build_export_plan` from 2-source to N-source |
| Generate N sources in style.json | **MEDIUM** | Refactor `_create_style_from_canvas` to loop over N sources |
| Per-source layer filtering | **HIGH** | `QgsMapSettings.setLayers()` already available |
| SMP format compatibility | **CONFIRMED HIGH** | Format supports unlimited sources; no hard limit |
| Data-defined overrides (continuous) | **LOW** | No automatic way to detect continuous style changes |

## 8. Critical Limitation: Data-Defined Overrides

QGIS supports data-defined overrides (expressions) on symbol properties like size, color, opacity. These can reference `@map_scale` or `@zoom_level` and change continuously:

```
symbol_size = scale_linear(@map_scale, 1000, 100000, 20, 2)
```

This creates a **continuous gradient** of visual change — every zoom level is technically different. The detection algorithm cannot automatically determine meaningful group boundaries for these cases.

**Recommendation:** Only detect discrete, configured scale boundaries (layer visibility, rule activation, label toggle). Ignore data-defined overrides.

---

## 9. SMP Format Specification (Investigated from styled-map-package)

### 9.1 Multiple Sources: No Hard Limit

The SMP specification places **no hard limit on the number of sources**. The `style.json` follows standard MapLibre Style Specification v8, where `sources` is a key-value map of source ID to source definition. Any number of entries is valid.

Sources are encoded using base-36 integers (`packages/api/lib/writer.js:518-520`):
```javascript
function encodeSourceId(sourceIndex) {
  return sourceIndex.toString(36)  // 0→"0", 1→"1", 35→"z", 36→"10"
}
```

### 9.2 Per-Source Zoom Ranges

Each source defines its own `minzoom` and `maxzoom`. The spec requires these (`spec/1.0/README.md:272-278`):

> Each tile source in `style.json` **MUST** include:
> - `bounds` — Bounding box `[west, south, east, north]` in WGS 84
> - `minzoom` — Minimum zoom level (non-negative integer)
> - `maxzoom` — Maximum zoom level (positive integer)
> - `tiles` — Array containing exactly one SMP URI template

**Overlapping zoom ranges are allowed.** The validator validates each source independently — it never compares zoom ranges across sources. The QGIS plugin test (`test/test_generator.py:4659-4691`) explicitly tests overlapping ranges.

### 9.3 Source Transitions: No Built-In Mechanism

The SMP specification defines **no transition/interpolation mechanism** between sources. Source switching behavior is entirely governed by MapLibre GL based on layer definitions:

1. **Layer order**: Layers render bottom-to-top; the last layer paints on top.
2. **Layer-level minzoom/maxzoom**: Each layer can have its own zoom visibility independent of the source's zoom range.
3. **`raster-opacity`**: Can be set per-layer (including via expressions) to control transparency.
4. **No cross-fade between different sources** — each layer renders independently.

The QGIS plugin's current two-source style uses both layers at `raster-opacity: 1`, meaning the region detail layer completely covers the world overview wherever both have tiles.

### 9.4 Tile Directory Structure

```
s/{source_index}/{z}/{x}/{y}.{ext}
```

Defined in `packages/api/lib/utils/templates.js:16`. The URI template uses `smp://maps.v1/s/{sourceId}/{z}/{x}/{y}.{ext}`.

Central directory ordering: tiles from **all sources** at zoom level N appear before any tiles at zoom level N+1, with sources interleaved within a zoom level.

### 9.5 MapLibre Style with N Raster Sources

Example from test fixture (`packages/api/test/fixtures/valid-styles/raster-sources.output.json`):
```json
{
  "version": 8,
  "sources": {
    "png-tiles": {
      "type": "raster", "tileSize": 256, "scheme": "xyz",
      "minzoom": 0, "maxzoom": 0,
      "bounds": [-180, -85.051129, 180, 85.051129],
      "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.png"]
    },
    "jpg-tiles": {
      "type": "raster", "tileSize": 256, "scheme": "xyz",
      "minzoom": 0, "maxzoom": 0,
      "bounds": [-180, -85.051129, 180, 85.051129],
      "tiles": ["smp://maps.v1/s/1/{z}/{x}/{y}.jpg"]
    }
  },
  "layers": [
    { "id": "jpg-tiles", "type": "raster", "source": "jpg-tiles", "minzoom": 0, "maxzoom": 22 },
    { "id": "png-tiles", "type": "raster", "source": "png-tiles", "minzoom": 0, "maxzoom": 22 }
  ],
  "metadata": {
    "smp:bounds": [-180, -85.051129, 180, 85.051129],
    "smp:maxzoom": 0,
    "smp:sourceFolders": {
      "png-tiles": "s/0",
      "jpg-tiles": "s/1"
    }
  }
}
```

### 9.6 Validation Rules

The validator checks per-source:
- Source must have `bounds`, `minzoom`, `maxzoom`, `tiles` (error)
- Source must NOT have `url` property (error)
- `scheme` must be `"xyz"` or absent (error)
- `tiles` must contain exactly one URL template (error)
- Tile URL must use `smp://maps.v1/` scheme (error)
- All tiles within bounds at zooms minzoom–maxzoom must exist (error)
- All tiles within a source must use the same format (error)

**No cross-source validation** — the validator never compares zoom ranges or bounds across sources.

### 9.7 SMP is Transport, Not Renderer

The SMP format is deliberately **renderer-agnostic**. It packages everything a MapLibre-compatible renderer needs into a single ZIP file, but delegates all rendering decisions to the consumer. The rendering pipeline is:

```
SMP File (ZIP) → Reader → HTTP Server → MapLibre GL
                  (opens)   (rewrites      (renders
                            smp:// to       using standard
                            http://)        compositing)
```

MapLibre GL handles all compositing, zoom transitions, and source selection. The SMP library's job is just to store tiles, generate correct URL templates, and set correct metadata.

---

## 10. How CoMapeo Renders Multi-Source SMP (Investigated)

### 10.1 Rendering Pipeline

The `styled-map-package` repo is a **library + CLI tool** — it does NOT contain a renderer. The rendering is delegated entirely to MapLibre GL. The flow is:

1. Reader opens the ZIP archive
2. Server serves `style.json` with `smp://` URIs rewritten to HTTP URLs
3. MapLibre GL loads the style and makes tile requests
4. Server maps tile requests to ZIP entries and streams tile data back
5. **MapLibre GL handles all compositing and zoom transitions**

### 10.2 Multi-Source Compositing Behavior

With multiple raster sources, MapLibre GL's behavior is:
- Each raster layer paints **in order of appearance** in the `layers` array
- If two raster layers cover the same zoom level and area, the **later layer paints on top**
- There is **no cross-fade between different sources** — each layer renders independently
- `raster-fade-duration` (default 300ms) only applies to parent/child tile transitions within the **same source**, not between sources

### 10.3 The Zoom Gap Problem

When sources have non-contiguous zoom ranges (e.g., world z0-3, region z5-10), **zoom level 4 has no tiles from either source**. MapLibre GL shows the background color at that zoom. The QGIS plugin design accepts this gap — users typically zoom quickly through it.

This is explicitly tested in `test/test_generator.py:4494-4524` (`TestZoomGapCase`).

### 10.4 CoMapeo Mobile Renderer (Verified from Source)

**CoMapeo mobile uses `@rnmapbox/maps` (Mapbox React Native SDK), not MapLibre.**

The rendering chain, verified from `comapeo-mobile` source:

1. **Backend** (`comapeo-mobile/src/backend/src/app.js:84-100`):
   - Registers `MapeoMapsFastifyPlugin` (from `@mapeo/core`) at `/maps` prefix
   - Plugin opens SMP file with `ReaderWatch`, serves via SMP Server

2. **Core** (`comapeo-core/src/fastify-plugins/maps.js:84-87`):
   - `SMPServerPlugin` serves `style.json` and tiles from the SMP ZIP
   - Rewrites all `smp://` URIs to `http://localhost:{port}/maps/custom/` URLs
   - **Zero source-specific logic** — serves any style.json as-is

3. **Frontend** (`comapeo-mobile/src/frontend/screens/MapScreen/index.tsx:102-159`):
   - `useMapStyleJsonUrl()` hook fetches style URL from core
   - Passes `styleURL` directly to `<Mapbox.MapView styleURL={styleUrl}>`
   - Mapbox SDK loads style, discovers sources, requests tiles, composites
   - **No source inspection or modification** — fully opaque to the app

4. **Style URL resolution** (`comapeo-core/src/fastify-plugins/maps.js:99-131`):
   - `GET /maps/style.json` tries: custom SMP → online Mapbox → fallback SMP
   - Returns first available via HTTP redirect
   - Custom SMP always takes priority when present

**Key finding**: CoMapeo mobile never inspects, counts, or modifies sources. It passes the `styleURL` to Mapbox SDK and the SDK handles everything. N raster sources work identically to 1 raster source from CoMapeo's perspective.

### 10.5 SMP Reader URL Rewriting (Verified)

At `styled-map-package/packages/api/lib/reader.js:249-281`, `getStyle()` rewrites ALL source tile URLs uniformly:

```javascript
for (const source of Object.values(style.sources)) {
  if ('tiles' in source && source.tiles) {
    source.tiles = source.tiles.map((tile) => getUrl(tile, baseUrl))
  }
}
```

Each source's `smp://maps.v1/s/{sourceId}/{z}/{x}/{y}.{ext}` becomes `http://localhost:PORT/maps/custom/s/{sourceId}/{z}/{x}/{y}.{ext}`. The Mapbox SDK then requests tiles from whichever source covers the current zoom level.

### 10.6 Existing Multi-Source Tests

In the styled-map-package repo:
- `packages/api/test/fixtures/valid-styles/raster-sources.input.json` — two raster sources (PNG + JPG)
- `packages/api/test/server.js:238-321` — multi-source fallback routing
- `packages/api/test/write-read.js:647-730` — two-source write-read verification

In the QGIS plugin repo:
- `test/test_generator.py:4494-4524` — `TestZoomGapCase` (disjoint zoom ranges)
- `test/test_generator.py:4716-4759` — `TestDedupWithOverlappingZooms`
- `test/test_generator.py:3931-3958` — `test_two_source_style_has_two_raster_layers`
- `test/test_generator.py:4242-4273` — `test_two_source_tiles_under_s_0_and_s_1`

---

## 11. End-to-End Rendering Pipeline (Verified)

```
┌─────────────────────────────────────────────────────────────────┐
│ QGIS Plugin (comapeo_smp_generator.py)                          │
│                                                                 │
│  1. _build_export_plan() creates 1 or 2 source plans            │
│  2. _render_single_tile() renders tiles per zoom level          │
│     - QGIS automatically applies scale-dependent rendering      │
│     - Each tile already looks correct for its zoom level        │
│  3. _create_style_from_canvas() generates style.json            │
│  4. _build_smp_archive() packages into ZIP                      │
│                                                                 │
│  Output: my-map.smp (ZIP containing style.json + s/{i}/{z}/...) │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ CoMapeo Core (comapeo-core/src/fastify-plugins/maps.js)         │
│                                                                 │
│  1. ReaderWatch opens my-map.smp                                │
│  2. SMPServerPlugin registered at /maps/custom                  │
│  3. GET /maps/custom/style.json                                 │
│     → Reader.getStyle(baseUrl)                                  │
│     → Rewrites smp:// → http://localhost:PORT/maps/custom/      │
│     → Returns style.json with N sources, each with http:// URLs │
│  4. GET /maps/custom/s/{sourceId}/{z}/{x}/{y}.{ext}            │
│     → Reader.getResource(path)                                  │
│     → Streams tile bytes from ZIP entry                         │
│                                                                 │
│  GET /maps/style.json → tries custom → online → fallback        │
│  Returns first available via HTTP 302 redirect                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ CoMapeo Mobile (comapeo-mobile/src/frontend/)                   │
│                                                                 │
│  1. useMapStyleJsonUrl() → api.getMapStyleJsonUrl()             │
│     → "http://localhost:PORT/maps/style.json"                   │
│  2. <Mapbox.MapView styleURL={styleUrl}>                        │
│     → Mapbox SDK loads style.json                               │
│     → Discovers N raster sources with http:// tile URLs         │
│     → At each zoom level, requests tiles from matching source   │
│     → Composites raster layers bottom-to-top per layers[] order │
│                                                                 │
│  Zero source-specific logic in the mobile app.                  │
│  N sources work identically to 1 source.                        │
└─────────────────────────────────────────────────────────────────┘
```

### Key Code References

| Component | File | Lines | Role |
|-----------|------|-------|------|
| Mobile map screen | `comapeo-mobile/src/frontend/screens/MapScreen/index.tsx` | 102-159 | Renders Mapbox.MapView with styleURL |
| Style URL hook | `comapeo-mobile/src/frontend/hooks/server/maps.ts` | 1-22 | Gets style URL from comapeo-core |
| Backend app init | `comapeo-mobile/src/backend/src/app.js` | 84-100 | Registers maps plugins with Fastify |
| Core maps plugin | `comapeo-core/src/fastify-plugins/maps.js` | 25-132 | Registers SMP Reader + Server |
| Core style endpoint | `comapeo-core/src/fastify-plugins/maps.js` | 99-131 | Tries custom → online → fallback |
| SMP Reader.getStyle | `styled-map-package/packages/api/lib/reader.js` | 249-281 | Rewrites smp:// URLs to http:// |
| SMP Server routing | `styled-map-package/packages/api/lib/server.js` | 68-160 | Serves style.json + tiles |
| Plugin export plan | `comapeo_smp_generator.py` | 419-477 | Builds 1 or 2 source plans |
| Plugin style gen | `comapeo_smp_generator.py` | 829-1006 | Generates style.json (1 or 2 sources) |
| Plugin tile render | `comapeo_smp_generator.py` | 1129-1220 | Renders individual tiles |

---

## 12. Key Architectural Insight: Rendering Already Works Correctly

**Critical realization**: The current plugin already renders tiles correctly at each zoom level. When QGIS renders a tile at zoom 4 vs zoom 12, it automatically applies different scale-dependent rules, layer visibility, and label settings because the map scale is different. The tiles at different zoom levels **already look different**.

The issue is not that rendering is wrong — it's that **all zoom levels are packed into a single source**. The auto-detection feature would:

1. **Introspect** the QGIS project to find scale-dependent rendering boundaries
2. **Group** zoom levels by their effective rendering configuration
3. **Split** the single source into N sources, one per rendering group
4. **Generate** the appropriate multi-source `style.json` with non-overlapping zoom ranges

The tiles themselves don't change — only how they're organized into sources in the archive.

**Important caveat**: The tiles already render correctly even without source splitting. A single source with all zoom levels will display correctly in MapLibre GL because the raster tiles at each zoom level already reflect the scale-appropriate rendering. Source splitting is about **organizational clarity and metadata**, not about fixing rendering output.

This raises the question: **Is source splitting actually necessary for CoMapeo, or is it a nice-to-have?** If CoMapeo renders correctly with a single source containing all zoom levels, the auto-detection feature provides metadata/organizational benefits but isn't strictly required for correct display.

---

## 13. Open Questions — All Answered

### Answered by SMP format investigation:
1. ~~Does CoMapeo's SMP renderer actually require separate sources?~~ **No — single source works fine. MapLibre GL renders correctly regardless.**
2. ~~How does CoMapeo handle source transitions?~~ **No cross-fade. Layer stacking with later layers on top. Zoom gaps show background color.**
3. ~~What are the SMP format's constraints on source count?~~ **No hard limit. ZIP64 supports unlimited entries. Recommended max 500,000 entries.**
4. ~~How does `style.json` express multiple raster sources?~~ **Standard MapLibre v8: N sources in `sources` object, N layers in `layers` array, each layer references one source.**
5. ~~Does CoMapeo support raster-opacity transitions?~~ **Not in the SMP library. MapLibre GL supports `raster-opacity` expressions, but the plugin doesn't use them currently.**

### Answered by CoMapeo mobile investigation:
6. ~~**Is the original question motivated by a CoMapeo requirement or a QGIS authoring concern?**~~ **Purely a QGIS authoring concern.** CoMapeo's rendering pipeline (comapeo-core → SMP Server → Mapbox SDK) handles any number of raster sources transparently. The mobile app has zero source-specific logic — it passes `styleURL` to `<Mapbox.MapView>` and the SDK handles everything. A single source with all zoom levels renders correctly because the raster tiles at each zoom level already reflect scale-appropriate rendering from QGIS. Auto-splitting provides no functional benefit to CoMapeo.

7. ~~**What is the expected user experience?**~~ **Opt-in is the safest choice.** The current `_build_export_plan` (`comapeo_smp_generator.py:419-477`) has a clear conditional pattern: `if include_world_base_zooms` creates 2 sources, else 1 source. Auto-detection should follow the same pattern — a new checkbox parameter that triggers the detection logic. Projects without scale-dependent features would produce a single source (identical to current behavior), so there's no risk of regression.

8. ~~**How should zoom gaps between sources be handled?**~~ **Gaps are acceptable and already handled.** The plugin already accepts zoom gaps in the world/region split — `TestZoomGapCase` at `test/test_generator.py:4494-4524` explicitly tests this. MapLibre/Mapbox shows the background color at zoom levels with no tiles. For auto-detected sources, the detection algorithm groups consecutive zoom levels with the same rendering signature, so gaps would only occur if a zoom level falls between two rendering groups with different signatures — which is unlikely since detection is based on discrete scale boundaries. The algorithm should ensure contiguous coverage by assigning each zoom to the nearest group.

9. ~~**Should the world/region split be combined with rendering-based splitting?**~~ **Yes, they compose naturally.** The current architecture at `_build_export_plan:419-477` has two orthogonal dimensions: (1) geographic split (world vs. region) based on extent, and (2) rendering split (proposed) based on layer/renderer configuration. They compose: world source (zoom 0–3) stays as-is using all visible layers; region sources (zoom 4–14) could be split into N sources based on rendering detection. The implementation would apply rendering detection only to the region source(s).

10. ~~**What is the interaction with the existing `include_world_base_zooms` option?**~~ **Auto-detection supplements it, doesn't replace it.** Looking at `_build_export_plan:424-444`, the four combinations are:
    - `include_world_base_zooms=True` + auto-detect: World source stays as-is. Region source gets split into N sources based on rendering detection.
    - `include_world_base_zooms=False` + auto-detect: Single source gets split into N sources.
    - `include_world_base_zooms=True` + no auto-detect: Current behavior (2 sources).
    - `include_world_base_zooms=False` + no auto-detect: Current behavior (1 source).

11. ~~**How should the generated source IDs be named?**~~ **Follow the existing descriptive pattern.** Current IDs are `"world-overview"`, `"region-detail"`, `"mbtiles-source"`. For auto-detected sources: use `"source-{index}"` for the `source_id` (used in URLs) with a descriptive `"name"` field like "Zoom 0–5 (3 layers)" vs "Zoom 6–14 (5 layers)". The SMP format stores both `source_id` (URL-safe, used in `smp://` URIs) and `name` (human-readable, displayed by CoMapeo). The `name` field is the right place for descriptive names.

---

## 14. Conclusions

### The Feature is Self-Contained in the QGIS Plugin

Auto-detection of zoom-dependent sources requires changes **ONLY in the QGIS plugin**. No changes are needed to:
- `styled-map-package` (already handles N sources)
- `comapeo-core` (already serves any SMP transparently)
- `comapeo-mobile` (already renders any style.json with N sources via Mapbox SDK)

### Rendering Splitting is an Authoring Concern, Not a Rendering Fix

Since QGIS already renders tiles correctly at each zoom level (scale-dependent rules are automatically applied), and CoMapeo already displays them correctly regardless of how many sources exist, the auto-detection feature is about:

1. **Metadata clarity** — each source has a descriptive name and defined zoom range
2. **Authoring intent** — the QGIS author's scale-dependent design is explicitly encoded in the SMP structure
3. **Potential future use** — if CoMapeo ever needs to treat different zoom ranges differently (e.g., downloading only certain sources), having separate sources enables this

### The `_create_style_from_canvas` Hardcoding is the Main Technical Debt

The biggest implementation challenge is at `comapeo_smp_generator.py:848`:

```python
if source_plans is not None and len(source_plans) == 2:
```

This hardcoded check for exactly 2 sources needs to become a loop over N sources. The style structure is repetitive (each source gets the same pattern of source definition + raster layer), so this is a straightforward generalization.

---

## 15. Recommended Implementation Phases

### Phase 1 — Scale-dependent layer visibility detection
Detect which layers have `hasScaleBasedVisibility() == True`, compute the scale for each zoom level, and group zooms by which set of layers is visible. Create one source per unique layer-set. This covers the most common use case (turning layers on/off at different scales).

### Phase 2 — Rule-based renderer introspection
Extend detection to `QgsRuleBasedRenderer` rules, grouping zooms by which rules are active. This handles the case where a single layer looks different at different scales.

### Phase 3 — Label introspection
Add label scale-dependency detection for both simple and rule-based labeling.

### Phase 4 — Smooth transitions (optional)
Add `raster-opacity` expressions in the generated style to create smooth cross-fades between sources at zoom boundaries.

---

## 16. Updated Feasibility Assessment

| Aspect | Feasibility | Confidence | Notes |
|--------|-------------|------------|-------|
| Detect layer scale visibility | **HIGH** | Confirmed | QGIS API directly exposes this |
| Detect rule-based renderer changes | **HIGH** | Confirmed | `dependsOnScale()`, `isScaleOK()` exist |
| Detect label scale changes | **HIGH** | Confirmed | `scaleVisibility`, `minimumScale` exist |
| Generate N sources in export plan | **MEDIUM** | Confirmed | Architecture supports it; needs refactoring |
| Generate N sources in style.json | **MEDIUM** | Confirmed | Currently hardcoded for 1 or 2 sources |
| CoMapeo compatibility | **HIGH** | **Verified end-to-end** | Mapbox SDK handles N sources transparently |
| No CoMapeo code changes needed | **HIGH** | **Verified** | Zero source-specific logic in comapeo-core or mobile |
| Data-defined overrides | **LOW** | Confirmed | Cannot detect continuous changes |

---

*Research status: Complete. All technical questions answered. Testing plan at [research-zoom-sources-testing.md](research-zoom-sources-testing.md). Ready for implementation planning after tests validate cross-fade hypothesis.*
