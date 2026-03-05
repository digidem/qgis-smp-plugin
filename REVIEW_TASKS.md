# QGIS SMP Plugin Code Review Tasks Tracker

## Critical Issues Blockers - Safety & Cross-Platform
- [x] **Fix Missing Cancellation Handling in Tile Generation Loop**
  - *Details:* `_generate_tiles_from_canvas` ignores `self.feedback.isCanceled()`. Needs a check inside the `as_completed(futures)` loop to break early and stop generation when the user clicks Cancel.
- [x] **Fix Windows Path Separators in ZIP Archive**
  - *Details:* `_build_smp_archive` uses `os.path.join` which creates backslashes on Windows. MapLibre requires POSIX forward slashes (`/`). Update to use `rel.replace(os.sep, '/')`.

## Non-Critical Issues & Suggestions - Cleanup & QA
- [x] **Remove Unused Imports & Dead Code**
  - *Details:* Clean up unused imports in `comapeo_smp.py`, `comapeo_smp_algorithm.py`, and `comapeo_smp_generator.py`. Evaluate if `SMPGeneratorTask` is dead code and remove if orphaned.
- [x] **Address Test Coverage Gaps**
  - *Details:* Add tests for progress cancellation (mocking `isCanceled()`) and ZIP archive paths (ensuring strict POSIX separators even on Windows).
