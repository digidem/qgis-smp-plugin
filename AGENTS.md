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
- Do not bump `metadata.txt` version or add a changelog entry unless the user explicitly asked for a release/version change. Every push to `main` triggers the release workflow — bumping the version is the release act itself.
- Do not rewrite large plugin-builder header blocks unless needed for the task.

## Release Process

Releases are triggered by publishing a GitHub Release. The workflow
(`.github/workflows/release.yml`) fires on `release: published`, validates that
the release tag matches `metadata.txt`, extracts the changelog for that version,
builds the plugin zip via `git archive`, and attaches it to the release.

**Steps to cut a release:**

1. **Bump the version** in `metadata.txt`:
   ```
   version=X.Y.Z
   ```

2. **Add a changelog entry** at the top of the `changelog=` block in
   `metadata.txt`, following the existing format:
   ```
   X.Y.Z - Short summary of the release
   * Bullet point describing each change
   * Another change
   ```

3. **Commit and push to `main`**:
   ```
   git commit -m "Release vX.Y.Z — <one-line summary>"
   git push
   ```

4. **Create the GitHub Release** at
   `https://github.com/digidem/qgis-smp-plugin/releases/new`:
   - Tag: `vX.Y.Z` (must exactly match `v` + version in `metadata.txt`)
   - Title: `Release vX.Y.Z`
   - Click **Publish release**

5. The workflow fires automatically: validates the tag, extracts the changelog
   as the release body, builds `comapeo_smp_vX.Y.Z.zip` via `git archive`,
   and attaches it to the release.

6. **Verify** the release at
   `https://github.com/digidem/qgis-smp-plugin/releases`.

**Important:**
- The tag name must be exactly `v` + the `version=` value in `metadata.txt`.
  If they don't match the workflow will fail with a clear error.
- Do not bump `metadata.txt` version unless this is a release commit.
- The zip is built with `git archive` and `.gitattributes` `export-ignore`
  rules strip dev files (tests, CI, tooling). Only plugin source files,
  `metadata.txt`, `LICENSE`, `README.md`, and `i18n/` are included.

## Commands

- Fast file search: `rg --files`
- Fast text search: `rg "pattern" -n`
- Reliable local logic tests (no QGIS runtime needed): `make test`,
  `make test-logic`, or `PYTHONPATH=. python3 test/test_generator.py`
- Headless QGIS integration test (uses real QGIS bindings, no GUI):
  `./scripts/test-qgis-headless.py` — verifies CRS transforms, style
  generation, archive build, dedup, and cancellation in an actual QGIS
  runtime. Uses system Python (`/usr/bin/python3`) which has PyQt5 and
  QGIS bindings. Requires `QT_QPA_PLATFORM=offscreen`.
- Install plugin into local QGIS for manual testing: `./install-dev.sh`
  then reload in QGIS (Plugin Reloader or restart).
- Full legacy QGIS test command (requires QGIS Python env + `nosetests`):
  `make test-legacy`
- Lint command (non-blocking by Makefile design): `make pylint`
- Style command (non-blocking by Makefile design): `make pep8`
- Security scan: `make bandit` (screen) or `make bandit-report` (JSON file)
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
- For headless QGIS testing (real CRS transforms, style generation, archive build): `./scripts/test-qgis-headless.py` — uses system Python (`/usr/bin/python3`) with QGIS bindings, runs offscreen.
- If `python3 -m unittest test...` fails outside a QGIS Python environment,
  remember that `test/__init__.py` imports `qgis` eagerly; that failure does
  not invalidate the QGIS-free logic tests.
- If command behavior is unclear, inspect with `make -n` before executing.
- If requirements conflict, follow explicit user instructions first and call out tradeoffs briefly.

## Approach
- Think before acting. Read existing files before writing code.
- Be concise in output but thorough in reasoning.
- Prefer editing over rewriting whole files.
- Do not re-read files you have already read unless the file may have changed.
- Test your code before declaring done.
- No sycophantic openers or closing fluff.
- Keep solutions simple and direct.
- User instructions always override this file.
