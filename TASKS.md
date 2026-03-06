# QGIS SMP Plugin Active Backlog

This file tracks unresolved work only. Historical completed items were removed to keep the backlog actionable.

Progress rule: when a task is completed, keep the checklist item, add the exact verification commands run, and note any remaining QGIS-runtime gaps.

## Blockers Before Release

- [x] Fix cancellation so QGIS Processing stops promptly and never reports a canceled run as success.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit 954c139 + review follow-up in working tree)
  - Changes: `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`
  - `_render_single_tile` accepts `cancel_event`; returns False immediately when set.
  - `_render_single_tile` now aborts active jobs when cancellation is signalled mid-render.
  - `_generate_tiles_from_canvas` submits futures lazily, sets `cancel_event`, and calls
    `future.cancel()` when `isCanceled()` fires.
  - `_build_smp_archive` now stops on cancellation and removes partial zip output.
  - `generate_smp_from_canvas` returns `None` when cancelled; archive is never built or kept.
  - `processAlgorithm` returns `{}` (not OUTPUT_FILE) when output_path is None.
  - QGIS-runtime gap: manual smoke test of cancelling a medium-sized export still needed.

- [x] Make cache/resume metadata thread-safe and cheap under parallel rendering.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit b951ceb + review follow-up in working tree)
  - Changes: `comapeo_smp_generator.py`
  - `TileCache` now shares a per-path lock across instances targeting the same cache dir.
  - `mark()` / `invalidate()` support deferred writes; generator flushes metadata after tile generation.
  - Cache freshness fingerprint now includes project CRS as part of render-state invalidation.
  - `_save()` writes atomically via a `.tmp` file + `os.replace()`.

- [x] Ensure cached exports never package internal cache files or stale tiles into the final `.smp`.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit a04ad3b + review follow-up in working tree)
  - Changes: `comapeo_smp_generator.py`
  - `_build_smp_archive` always excludes `_cache_meta.json`.
  - When `cache_dir` is active, only tiles in the current tile manifest are zipped.
  - Resume freshness now includes project render-state fingerprinting, not just format/quality.
  - Save failures now raise instead of silently marking tiles fresh.

- [x] Make map content deterministic in Processing contexts.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit 954c139 + review follow-up in working tree)
  - Changes: `comapeo_smp_generator.py`
  - `_generate_tiles_from_canvas` now sources layers from `root.findLayers()` (layer-tree
    order, top-to-bottom) instead of `project.mapLayers().values()` (dict/arbitrary order).
  - When custom layer order is enabled in the project, export now uses that ordering.
  - QGIS-runtime gap: manual comparison of layer order in canvas vs exported tiles still needed.

- [x] Clamp low-zoom style output and harden Processing parameter validation.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit 834f4fa + review follow-up in working tree)
  - Changes: `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`
  - `default_zoom` in style.json now stays within the available source range, including `min_zoom`.
  - `TILE_FORMAT` validation now rejects non-integer values before doing range checks.

## Test And Release Reliability

- [x] Replace misleading test guidance with commands that actually fail on errors.
  - Verification run: `make test` exits 0 with 92 tests OK; `make -n deploy` shows corrected plugin path (commit 9f0f0a6 + review follow-up in working tree)
  - Changes: `Makefile`, `README.md`, `AGENTS.md`
  - `make test` is now the reliable QGIS-free entrypoint.
  - Legacy full-suite behavior moved to `make test-legacy`.
  - README and `AGENTS.md` updated to stop treating the false-green legacy target as normal.

- [x] Expand tests to cover real cache/resume/archive behavior and tile write failures.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit fdef4c5 + review follow-up in working tree)
  - Changes: `test/test_generator.py`
  - Added: TestLowZoomStyleOutput, TestCancelEventInRenderSingleTile,
    TestGenerateSmpCancellation, TestSMPArchiveExcludesCacheMetadata,
    TestTileSaveFailure, TestTileCacheThreadSafety, TestDeterministicLayerOrder,
    TestPluginLifecycle

- [x] Align user-facing docs and metadata with the actual raster-only implementation.
  - Verification run: manual review against `comapeo_smp_generator.py` and `metadata.txt` (commit 9c6d160 + review follow-up in working tree)
  - Changes: `README.md`, `metadata.txt`
  - README: SMP described as raster tiles + style.json (no glyphs/sprites/vectors).
  - README: plugin name corrected to "CoMapeo Map Builder" in search instructions.
  - README: development install instructions now describe symlinking the repo root as `comapeo_smp`.
  - README: test section updated to recommend `make test`.
  - metadata.txt: removed stale "error threshold (>50000)" from 0.3.0 changelog.
  - metadata.txt: tags updated to `raster tiles`; changelog clarifies the JPG default is a Processing UI default.

- [x] Fix stale packaging automation so local package/deploy targets include the real plugin modules.
  - Verification run: `make -n deploy` shows the correct plugin path and no empty copy commands (commit 9f0f0a6 + review follow-up in working tree)
  - Changes: `Makefile`
  - PY_FILES now includes `comapeo_smp_provider.py`, `comapeo_smp_algorithm.py`,
    `comapeo_smp_generator.py`.
  - `QGIS_PLUGIN_DIR` now resolves to the actual plugin directory, and deploy/zip use it directly.
  - Empty `UI_FILES` / `COMPILED_RESOURCE_FILES` no longer generate invalid `cp` invocations.
  - `EXTRA_DIRS` copy commands now remain inside the `deploy` recipe instead of being dropped at parse time.

## Maintenance Follow-Ups

- [x] Guard provider lifecycle against duplicate init/unload edge cases.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 92 tests OK (commit 9c6d160 + review follow-up in working tree)
  - Changes: `comapeo_smp.py`
  - `initProcessing()` only stores the provider reference if `addProvider()` succeeds.
  - `unload()` only clears the provider reference if `removeProvider()` succeeds.
  - Added regression coverage for failed add/remove and retry-after-failure.

- [x] Remove dead code and unused imports from the generator module.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit 834f4fa)
  - Changes: `comapeo_smp_generator.py`
  - Removed: `QgsRasterLayer`, `QgsVectorLayer` imports.
  - `TileCache.invalidate()` retained with justification docstring (public API for callers).

## Review Gates

- [x] Review Blockers Before Release after implementation.
  - All five blocker tasks complete with passing tests.
  - Cancellation: lazy submit + cancel_event + None return + {} from processAlgorithm.
  - Archive: META_FILE excluded; stale tiles filtered via tile_paths manifest.
  - Determinism: findLayers() used; QGIS-runtime manual test still a gap.

- [x] Review Test And Release Reliability after implementation.
  - `make test` now fails on real errors; `make test-legacy` is explicitly legacy.
  - 92 tests cover cancel, cache thread-safety, archive exclusion, low-zoom, layer order,
    lifecycle guards, and save-failure propagation.
  - Docs and packaging are accurate.

- [x] Final release-readiness review.
  - All blocker tasks complete (commits 834f4fa, b951ceb, a04ad3b, 954c139).
  - All reliability tasks complete (commits fdef4c5, 9f0f0a6, 9c6d160).
  - Verification: `make test` and `PYTHONPATH=. python3 test/test_generator.py` → 92 tests OK.
  - Remaining QGIS-runtime-only gaps (require manual smoke test in QGIS):
    1. Cancelling a medium-sized export stops promptly with no partial .smp.
    2. Layer rendering order in exported tiles matches QGIS layer panel order.
