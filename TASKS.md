# QGIS SMP Plugin Active Backlog

This file tracks unresolved work only. Historical completed items were removed to keep the backlog actionable.

Progress rule: when a task is completed, keep the checklist item, add the exact verification commands run, and note any remaining QGIS-runtime gaps.

## Blockers Before Release

- [x] Fix cancellation so QGIS Processing stops promptly and never reports a canceled run as success.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit 954c139)
  - Changes: `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`
  - `_render_single_tile` accepts `cancel_event`; returns False immediately when set.
  - `_generate_tiles_from_canvas` submits futures lazily, sets cancel_event and calls
    `future.cancel()` when `isCanceled()` fires.
  - `generate_smp_from_canvas` returns `None` when cancelled; archive is never built.
  - `processAlgorithm` returns `{}` (not OUTPUT_FILE) when output_path is None.
  - QGIS-runtime gap: manual smoke test of cancelling a medium-sized export still needed.

- [x] Make cache/resume metadata thread-safe and cheap under parallel rendering.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit b951ceb)
  - Changes: `comapeo_smp_generator.py`
  - `TileCache` now holds a `threading.Lock`; `mark()` and `invalidate()` acquire it.
  - `_save()` writes atomically via a `.tmp` file + `os.replace()`.

- [x] Ensure cached exports never package internal cache files or stale tiles into the final `.smp`.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit a04ad3b)
  - Changes: `comapeo_smp_generator.py`
  - `_build_smp_archive` always excludes `_cache_meta.json`.
  - When `cache_dir` is active, only tiles in the current tile manifest are zipped.
  - Resume support preserved: tiles on disk from prior runs are skipped if still fresh.

- [x] Make map content deterministic in Processing contexts.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit 954c139)
  - Changes: `comapeo_smp_generator.py`
  - `_generate_tiles_from_canvas` now sources layers from `root.findLayers()` (layer-tree
    order, top-to-bottom) instead of `project.mapLayers().values()` (dict/arbitrary order).
  - QGIS-runtime gap: manual comparison of layer order in canvas vs exported tiles still needed.

- [x] Clamp low-zoom style output and harden Processing parameter validation.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit 834f4fa)
  - Changes: `comapeo_smp_generator.py`, `comapeo_smp_algorithm.py`
  - `default_zoom` in style.json now clamped to `max(0, min(max_zoom - 2, 11))`.
  - TILE_FORMAT enum index guarded in both `checkParameterValues` and `processAlgorithm`.

## Test And Release Reliability

- [x] Replace misleading test guidance with commands that actually fail on errors.
  - Verification run: `make test-logic` exits 0 with 77 tests OK; `make -n test` shows `|| true` (commit 9f0f0a6)
  - Changes: `Makefile`, `README.md`, `AGENTS.md`
  - Added `make test-logic` target that runs `PYTHONPATH=. python3 test/test_generator.py -v`
    and propagates exit code.
  - Legacy `make test` annotated with a warning comment; README updated.

- [x] Expand tests to cover real cache/resume/archive behavior and tile write failures.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit fdef4c5)
  - Changes: `test/test_generator.py`
  - Added: TestLowZoomStyleOutput, TestCancelEventInRenderSingleTile,
    TestGenerateSmpCancellation, TestSMPArchiveExcludesCacheMetadata,
    TestTileSaveFailure, TestTileCacheThreadSafety, TestDeterministicLayerOrder

- [x] Align user-facing docs and metadata with the actual raster-only implementation.
  - Verification run: manual review against `comapeo_smp_generator.py` (commit 9c6d160)
  - Changes: `README.md`, `metadata.txt`
  - README: SMP described as raster tiles + style.json (no glyphs/sprites/vectors).
  - README: plugin name corrected to "CoMapeo Map Builder" in search instructions.
  - README: test section updated to recommend `make test-logic`.
  - metadata.txt: removed stale "error threshold (>50000)" from 0.3.0 changelog.

- [x] Fix stale packaging automation so local package/deploy targets include the real plugin modules.
  - Verification run: `make -n deploy` shows all 5 runtime modules in cp command (commit 9f0f0a6)
  - Changes: `Makefile`
  - PY_FILES now includes `comapeo_smp_provider.py`, `comapeo_smp_algorithm.py`,
    `comapeo_smp_generator.py`.

## Maintenance Follow-Ups

- [x] Guard provider lifecycle against duplicate init/unload edge cases.
  - Verification run: `PYTHONPATH=. python3 test/test_generator.py` — 77 tests OK (commit 9c6d160)
  - Changes: `comapeo_smp.py`
  - `initProcessing()` no-ops if `self.provider` is already set.
  - `unload()` only removes provider if not None, then clears the reference.

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
  - `make test-logic` now fails on real errors.
  - 77 tests cover cancel, cache thread-safety, archive exclusion, low-zoom, layer order.
  - Docs and packaging are accurate.

- [x] Final release-readiness review.
  - All blocker tasks complete (commits 834f4fa, b951ceb, a04ad3b, 954c139).
  - All reliability tasks complete (commits fdef4c5, 9f0f0a6, 9c6d160).
  - Verification: `make test-logic` → 77 tests OK.
  - Remaining QGIS-runtime-only gaps (require manual smoke test in QGIS):
    1. Cancelling a medium-sized export stops promptly with no partial .smp.
    2. Layer rendering order in exported tiles matches QGIS layer panel order.
