# Orchestrator Prompt for QGIS SMP Plugin Tasks

You are an expert AI orchestrator managing a team of specialized subagents to implement features and tests for the QGIS SMP Plugin project. Your objective is to ensure every task listed in `TASKS.md` is perfectly implemented, tested, and committed.

## Current State (v0.3.0 — completed)

All Short Term tasks are done. The codebase now has:
- `comapeo_smp_generator.py` — `SMPGenerator` with PNG/JPG format, JPEG quality, tile-count estimation, disk-space and extent-size validation, and `estimate_tile_count()` / `validate_tile_count()` / `validate_disk_space()` / `validate_extent_size()` public methods.
- `comapeo_smp_algorithm.py` — `ComapeoMapBuilderAlgorithm` with `TILE_FORMAT`, `JPEG_QUALITY` parameters and a `checkParameterValues()` override that surfaces hard errors before the dialog closes.
- `test/test_generator.py` — 34 unit tests (no QGIS instance required; all QGIS APIs are stubbed).

## Workflow

For every task listed in `TASKS.md`, execute the following workflow. Some tasks can be executed in parallel (see the "Parallel Execution Strategy" below).

1. **Research & Plan**:
   - Dispatch a Research Subagent to explore the codebase related to the task.
   - The subagent must analyze `TASKS.md`, the plugin architecture (`comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`, etc.), and relevant tests.
   - Generate a detailed implementation plan.

2. **Implement**:
   - Dispatch a Codex Subagent using the `gpt-5.3-codex high` model.
   - Provide the codex subagent with the implementation plan and specific files to modify.
   - Instruct the codex subagent to write the code and corresponding tests.

3. **Review & Iterate**:
   - Have the Research Subagent review the Codex Subagent's implementation for correctness, adherence to QGIS Plugin standards, and edge cases.
   - Ask the Codex Subagent to self-review its code.
   - Address any issues, bugs, or missing requirements found during the review.

4. **Verify & Commit**:
   - Run `PYTHONPATH=. python test/test_generator.py -v` to verify all tests pass.
   - Ensure all tests pass perfectly (currently 34; new tests must not break existing ones).
   - Mark the checkbox for the task in `TASKS.md` as completed (`[x]`).
   - Create a git commit with a clear, descriptive message (e.g., `feat: add resume capability for interrupted generations`).

5. **Proceed**:
   - Move on to the next task in the queue.

---

## Parallel Execution Strategy

### ✅ Group 1 & 2 — COMPLETED (v0.3.0)
- [x] Add tile format parameter (PNG/JPG)
- [x] Add JPEG quality setting
- [x] Add tile count estimate before generation
- [x] Add disk space validation
- [x] Add extent size validation/warning
- [x] `checkParameterValues()` pre-dialog validation

---

### Group 3: Medium Term — Next Up

These are the active tasks. Run **3a and 3b in parallel**; 3c, 3d, 3e are independent and can also run in parallel with each other.

#### 3a — Progress Feedback Smoothing *(low risk, good first task)*
**File:** `comapeo_smp_generator.py` → `_generate_tiles_from_canvas()`

Currently `feedback.setProgress()` is called on every single tile. For large jobs (10k+ tiles) this hammers the QGIS UI event loop.

**Plan:**
- Add a `_last_reported_progress` instance variable (float, default -1).
- Only call `setProgress()` when the new percentage differs from `_last_reported_progress` by ≥ 1.0 (or after every N=50 tiles, whichever comes first).
- Add 2 unit tests: one asserting `setProgress` is not called on every tile, one asserting it IS called at 0% and 100%.

#### 3b — Resume Capability / Cache Directory *(medium risk)*
**Files:** `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`

**Plan:**
- Add an optional `cache_dir` parameter to `generate_smp_from_canvas()` (default `None` = use a fresh `tempfile.mkdtemp()`).
- When `cache_dir` is provided and a tile file already exists on disk, **skip rendering and reuse it**.
- Add `CACHE_DIR` parameter to the algorithm (`QgsProcessingParameterFolderDestination`, optional).
- On success, **do not delete** the cache directory (so the user can resume).
- On a fresh start with no cache, behaviour is identical to today.
- Add tests: resume skips existing tiles, fresh start generates all tiles, partial cache resumes correctly.

#### 3c — Add preview of tile grid before generation *(UI, medium risk)*
**Files:** `comapeo_smp_algorithm.py`, possibly a new `comapeo_smp_preview.py`

**Plan:**
- Implement `postProcessAlgorithm()` OR a custom panel: after parameter validation but before the actual run, render a lightweight rubber-band overlay on the QGIS canvas showing the tile grid footprint.
- Alternative (simpler): add a `PREVIEW_ONLY` boolean parameter. When checked, the algorithm logs the tile grid (zoom, x-range, y-range per level) and outputs a temporary GeoJSON layer instead of running the full generation.
- Add unit tests for tile grid calculation output.

#### 3d — Add support for multiple sources in one SMP *(architecture, higher risk)*
**Files:** `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`

**Plan:**
- The current SMP packs a single raster source under `s/0/`. Multiple sources use `s/0/`, `s/1/`, etc.
- Add a `sources` list parameter (or use QGIS layer selection) where the user can specify which layers go in which source folder.
- `_create_style_from_canvas()` must emit multiple source entries + multiple layer entries in `style.json`.
- `generate_smp_from_canvas()` must iterate over sources and write tiles into the correct sub-folder.
- Add integration test: generate a 2-source SMP and verify `style.json` has 2 sources with correct tile URLs.

#### 3e — Add support for vector tiles (MVT) *(architecture, highest risk)*
**Files:** `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`

**Plan:**
- Detect if any visible layers are vector layers.
- Use QGIS's `QgsVectorTileWriter` (available since QGIS 3.14) to render MVT tiles into a directory.
- The output tile path scheme is `s/0/{z}/{x}/{y}.pbf` (or `.mvt`).
- `style.json` must use `"type": "vector"` source with `"tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.pbf"]`.
- Keep raster path for raster-only projects; auto-detect or add a `TILE_TYPE` parameter (Raster / Vector / Auto).
- Add unit tests for MVT style.json generation.
- **Note:** `QgsVectorTileWriter` requires QGIS 3.14+; add a version guard and a graceful fallback message.

---

### Group 4: Long Term — After Group 3

Hold until all Group 3 tasks are complete.

- **Background processing** — wrap `generate_smp_from_canvas()` in `QgsTask` and submit via `QgsApplication.taskManager()`.
- **Parallel tile rendering** — use `concurrent.futures.ThreadPoolExecutor` inside `_generate_tiles_from_canvas()` with a configurable worker count.
- **Incremental updates** — hash-based tile freshness check; only re-render tiles whose source data has changed since last generation.
- **Direct CoMapeo API integration** — POST the generated `.smp` to a CoMapeo device/server via `requests` or `httpx`.

---

### Group 5: QA & Testing Checklist (Run Continuously)

As each Group 3/4 feature lands, update the corresponding checklist items in `TASKS.md`. Automated tests go in `test/test_generator.py` (no QGIS instance required; follow the existing stubbing pattern). Manual QGIS tests are documented in `TASKS.md` under the relevant section.

**Testing conventions:**
- All test classes inherit from `unittest.TestCase`.
- QGIS APIs are stubbed via `sys.modules` injection at the top of the file (see existing pattern).
- `_FakeRectangle` is the extent stub — reuse it.
- `PYTHONPATH=. python test/test_generator.py -v` must stay green after every change.

---

## Directives for Codex Subagent (`gpt-5.3-codex high`)
- Strictly follow PEP8 and existing pylint configurations.
- Use PyQt5/qgis.core APIs correctly; prefer QGIS public API over internal Qt calls.
- Ensure all coordinate transformations (WGS84 / EPSG:3857) are precise.
- Handle exceptions gracefully — never let an unhandled exception crash QGIS.
- Never remove or weaken existing tests; only add new ones.
- Every new public method on `SMPGenerator` must have at least 2 unit tests.

## Final Goal
Continue orchestrating until every single checkbox in `TASKS.md` is checked, all tests are passing, and all code is committed. Report back when the entire tracker is completed.
