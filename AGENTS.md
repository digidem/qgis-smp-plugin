# AGENTS.md

Operational instructions for coding agents working in this repository.

## Do

- Keep changes focused and minimal for the requested task.
- Preserve QGIS Processing parameter IDs in `comapeo_smp_algorithm.py` unless a migration is explicitly requested.
- Parameter IDs to keep stable: `EXTENT`, `MIN_ZOOM`, `MAX_ZOOM`, `TILE_FORMAT`, `JPEG_QUALITY`, `OUTPUT_FILE`.
- Add or update tests when changing tile math, bounds logic, thresholds, or parameter validation.
- Add or update tests when changing cancellation, cache/resume behavior, archive contents, or layer selection/order.
- Keep `.smp` archives free of internal cache artifacts such as `_cache_meta.json`, and ensure cache-backed exports only package tiles for the current run.
- Prefer deterministic checks that fail on error and report exact commands run.
- Update docs when behavior or user-facing options change (`README.md`, `XYZ_SMP.md`, `metadata.txt` changelog if releasing).

## Don't

- Do not use `make test-legacy` as a success signal; it keeps the old `|| true`
  behavior and can pass even when tests fail or `nosetests` is missing.
- Do not use `make deploy` or `make zip` as evidence that packaging is correct
  without first checking the file lists; current legacy targets can omit core
  runtime modules.
- Do not run deployment or destructive make targets unless explicitly requested: `make deploy`, `make dclean`, `make derase`, `make zip`, `make upload`.
- Do not bump `metadata.txt` version or edit release workflow details unless the user asked for a release/version change.
- Do not rewrite large plugin-builder header blocks unless needed for the task.

## Commands

- Fast file search: `rg --files`
- Fast text search: `rg "pattern" -n`
- Reliable local logic tests (no QGIS runtime needed): `make test`,
  `make test-logic`, or `PYTHONPATH=. python3 test/test_generator.py`
- Full legacy QGIS test command (requires QGIS Python env + `nosetests`):
  `make test-legacy`
- Lint command (non-blocking by Makefile design): `make pylint`
- Style command (non-blocking by Makefile design): `make pep8`
- Package build: `make package VERSION=X.Y.Z`

## Safety and Permissions

- Ask before any network-dependent install/update command.
- Ask before commands that write outside the repository tree.
- Ask before destructive filesystem actions (`rm -rf`, deleting deployed plugin paths, or bulk cleanup in user directories).
- Prefer dry-run/read commands first when validating Makefile behavior (`make -n <target>`).

## Project Structure Hints

- Plugin entrypoint and provider wiring: `__init__.py`, `comapeo_smp.py`, `comapeo_smp_provider.py`.
- Processing UI and validation logic: `comapeo_smp_algorithm.py`.
- SMP generation, tile math, CRS transforms, style output: `comapeo_smp_generator.py`.
- Fastest reliable tests: `test/test_generator.py` (uses QGIS stubs).
- QGIS-runtime tests: `test/test_qgis_environment.py` and related integration tests.
- Packaging and automation: `Makefile`, `metadata.txt`, `.github/workflows/release.yml`.

## PR Checklist

- Changed code is scoped to the request and avoids unrelated refactors.
- Relevant tests were added/updated for behavioral changes.
- At least one verification command was executed and results were reported.
- Documentation updated if parameters, output format, or workflow changed.
- Version/changelog updates in `metadata.txt` only when release work is requested.

## When Stuck

- If QGIS runtime is unavailable, run `PYTHONPATH=. python3 test/test_generator.py` and clearly note QGIS-dependent gaps.
- If `python3 -m unittest test...` fails outside a QGIS Python environment,
  remember that `test/__init__.py` imports `qgis` eagerly; that failure does
  not invalidate the QGIS-free logic tests.
- If command behavior is unclear, inspect with `make -n` before executing.
- If requirements conflict, follow explicit user instructions first and call out tradeoffs briefly.
