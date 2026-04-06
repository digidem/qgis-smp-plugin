# UPDATE.md — Upstream Sync Tracker

Sourced from analysis of `../styled-map-package/` (SMP spec v1.0, commit `45e1767`).
Ordered easiest → hardest. All details preserved so agents can work without re-research.

---

## Task 1: Add VERSION file to SMP archives

**Status**: done
**Difficulty**: Easy — 2-line change
**Files**: `comapeo_smp_generator.py` (~line 1102, method `_build_smp_archive`)
**Test**: `test/test_generator.py` — add assertion for VERSION entry

### Context

Upstream writer (`packages/api/lib/writer.js:400`) writes a `VERSION` file containing `"1.0"` to every SMP archive. The SMP 1.0 spec (`spec/1.0/README.md`) defines this as part of the format. The upstream validator (`packages/api/lib/validator.js:236-243`) emits `warn('missing_version')` when absent. The reader (`reader.js:238-239`) defaults to `"1.0"` as a fallback, so current files still work — but they're not spec-compliant.

### What to change

In `_build_smp_archive`, after creating the ZipFile and before writing `style.json`, add:

```python
zipf.writestr('VERSION', '1.0')
```

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Add a test that opens the generated `.smp` (zip) and asserts `VERSION` exists with content `"1.0"`
- Optionally run upstream `smp validate` on a produced archive

---

## Task 2: Fix `smp:sourceFolders` value format

**Status**: done
**Difficulty**: Easy — 1-line change
**Files**: `comapeo_smp_generator.py` (~line 699, method that builds style.json metadata)
**Test**: `test/test_generator.py` — assert the value is `"s/0"` not `"0"`

### Context

The SMP 1.0 spec (section 4.3.3) says `smp:sourceFolders` maps source IDs to "folder path within the archive (relative to the archive root)". Upstream writer (`writer.js:490`) writes:

```javascript
metadata['smp:sourceFolders'][sourceId] = SOURCES_FOLDER + '/' + encodedSourceId
// Result: { "mbtiles-source": "s/0" }
```

The QGIS plugin currently writes just the subfolder name:

```python
"smp:sourceFolders": { source_id: "0" }  # Missing "s/" prefix
```

The upstream reader doesn't rely on this field for tile lookup (uses the `tiles` URL template), so this is non-breaking but spec-noncompliant.

### What to change

At `comapeo_smp_generator.py:699`, change:

```python
# FROM
source_id: "0"
# TO
source_id: "s/0"
```

Note: the source ID is currently hardcoded as `"mbtiles-source"` (line ~665). The folder prefix should match what's actually in the archive — tiles are stored under `s/0/{z}/{x}/{y}.{ext}`.

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Check style.json metadata in generated archive: `smp:sourceFolders` value should be `"s/0"`

---

## Task 3: Remove bogus `center` from source definition
**Status**: done
**Difficulty**: Easy — remove 1 line
**Files**: `comapeo_smp_generator.py` (~line 673)
**Test**: `test/test_generator.py` — assert source object has no `center` key

### Context

The QGIS plugin writes a `center: [0, 0, 6]` property inside the source object at `comapeo_smp_generator.py:673`. This is a TileJSON-legacy field that does not belong in a MapLibre GL source definition (MapLibre spec only defines `type`, `tiles`, `minzoom`, `maxzoom`, `bounds`, `scheme`, etc. for raster sources). The hardcoded values `[0, 0, 6]` are also wrong — they don't reflect the actual map center.

Upstream writer (`writer.js:425-493`) never writes `center` on the source object. It only writes it on the root style object.

The correct top-level `center` and `zoom` are already set at lines ~705-706, so removing the source-level one is safe.

### What to change

Delete line ~673:

```python
"center": [0, 0, 6],
```

Also consider removing the other non-standard source properties if desired (`format`, `name`, `version`) — these are harmless (spec says unknown props preserved) but add clutter. Low priority.

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Inspect style.json in generated archive: source should not have `center` key

---

## Task 4: Use ZIP_STORED for pre-compressed raster tiles

**Status**: done
**Difficulty**: Easy-Medium — small refactor in `_build_smp_archive`
**Files**: `comapeo_smp_generator.py` (method `_build_smp_archive`, ~line 1102)
**Test**: `test/test_generator.py` — check compression method on tile entries

### Context

The SMP 1.0 spec (section 3.3) says "Raster tile files SHOULD be stored using ZIP store mode" because PNG and JPEG are already compressed. The upstream writer uses `store: true` for tile entries. The QGIS plugin uses `ZIP_DEFLATED` for everything, causing double-compression with negligible size savings and slower write times.

Upstream reader (`reader.js`) supports both ZIP_STORED and ZIP_DEFLATED, so this is a performance-only fix.

### What to change

In `_build_smp_archive` (~line 1102), change the tile-writing loop to use `zipfile.ZIP_STORED` for tile files (`.jpg`, `.png`) while keeping `ZIP_DEFLATED` for `style.json` and `VERSION`.

Current code (simplified):
```python
with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    # ... style.json ...
    # ... tiles via os.walk ...
```

Proposed approach:
```python
with zipfile.ZipFile(output_path, 'w') as zipf:
    zipf.write(style_path, 'style.json', compress_type=zipfile.ZIP_DEFLATED)
    zipf.writestr('VERSION', '1.0')
    for tile_path in tile_paths:
        arcname = os.path.relpath(tile_path, tiles_dir)
        # Tiles already compressed (PNG/JPG), use STORE
        zipf.write(tile_path, f's/0/{arcname}', compress_type=zipfile.ZIP_STORED)
```

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Open generated `.smp` and check tile entries: `compress_type` should be `zipfile.ZIP_STORED`
- Check `style.json` and `VERSION`: should still be `ZIP_DEFLATED`
- Compare file sizes — should be nearly identical (possibly slightly smaller without double-compression overhead)

---

## Task 5: Add WebP tile format support

**Status**: done
**Files**: `comapeo_smp_algorithm.py` (TILE_FORMAT options), `comapeo_smp_generator.py` (render + style + archive)
**Test**: `test/test_generator.py` — test WebP path through format selection

### Context

The SMP 1.0 spec lists `.webp` as a supported tile extension. Upstream writer handles WebP tiles. The QGIS plugin currently only supports PNG and JPEG (`TILE_FORMAT_OPTIONS` in `comapeo_smp_algorithm.py`).

WebP can offer 25-35% smaller files than JPEG at similar quality, and supports transparency like PNG with better compression.

### What to change

1. **`comapeo_smp_algorithm.py`**: Add `"WEBP"` to `TILE_FORMAT_OPTIONS` with quality parameter
2. **`comapeo_smp_generator.py`**:
   - In `_render_single_tile`: render to WebP when format is `"WEBP"` (Qt supports `"WEBP"` via `QImage.save()` with format string `"WEBP"`)
   - In style.json generation: set tile extension to `.webp`
   - In `_build_smp_archive`: handle `.webp` extension

### Caveats

- Qt WebP support depends on the Qt build — need to verify QGIS's Qt has WebP enabled
- WebP quality parameter range differs from JPEG (0-100 for both, but perceptual quality differs)
- Consider making this opt-in or with a fallback warning if Qt doesn't support WebP

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Test with each format: PNG, JPG, WEBP
- Verify style.json `tiles` URL uses correct extension
- Check archive contains `.webp` tile files

---

## Task 6: Tile deduplication

**Status**: done
**Difficulty**: Medium-Hard — requires content hashing and central directory approach
**Files**: `comapeo_smp_generator.py` (new dedup logic in `_build_smp_archive`)
**Test**: `test/test_generator.py` — test that duplicate tiles share storage

### Context

Upstream added SHA-256 based deduplication (`writer.js:315-331`). When enabled, tiles with identical content are stored only once in the archive. This is useful for low-zoom tiles where large areas share the same uniform color (e.g., ocean tiles, empty land tiles).

For the QGIS plugin, this could significantly reduce file sizes for exports with large uniform areas at low zoom levels.

### What to change

In `_build_smp_archive`, before writing tile entries:

1. Compute SHA-256 hash of each tile's bytes
2. Maintain a `hash → archive_path` map
3. For duplicate hashes, write the first tile normally, then add subsequent entries pointing to the same data using `ZipInfo` with `compress_type=ZIP_STORED`
4. Note: Python's `zipfile` doesn't natively support dedup — you need to write the raw bytes once and create multiple `ZipInfo` entries referencing the same offset, OR simply skip duplicate tiles and create a mapping

Simpler alternative: hash tiles, store unique content once, create multiple `ZipInfo` entries with same `header_offset`. This requires using the lower-level `zipfile` API.

### Caveats

- Python's `zipfile` module doesn't have native dedup support — need to work with `ZipInfo` objects directly
- Trade-off: CPU time for hashing vs. disk space saved
- Should be optional (flag or always-on with a threshold)
- Low-zoom uniform tiles benefit most; high-zoom tiles are usually unique

### Verification

- Run `PYTHONPATH=. python3 test/test_generator.py`
- Create a test with intentionally duplicate tiles, verify archive size is smaller
- Verify all tile paths still resolve correctly when extracted

---

## Task 7: Integrate upstream validator into test suite

**Status**: done
**Difficulty**: Medium — requires Node.js runtime or Python reimplementation
**Files**: `test/test_generator.py` or new `test/test_validator.py`
**Dependencies**: Node.js + `styled-map-package-api` installed, or Python validation logic

### Context

Upstream has a full SMP validator (`packages/api/lib/validator.js`) that checks:
- VERSION file presence
- style.json structure and required fields
- `smp:sourceFolders` correctness
- Tile completeness (all tiles referenced in style exist)
- Resource integrity
- Error categorization: fatal, rendering, spec

Running this as part of the QGIS plugin's test suite would catch format regressions automatically.

### Approach options

**Option A (Recommended)**: Add a shell test that runs `npx smp validate` on a generated `.smp` file. Requires Node.js in CI.

**Option B**: Port a subset of validation checks to Python and run as part of `test_generator.py`. More self-contained but duplicates logic.

**Option C**: Use the upstream API programmatically via Node.js subprocess in tests.

### What to validate

At minimum, validate after generation:
- VERSION file exists and is `"1.0"`
- style.json is valid JSON with required fields (`version`, `sources`, `layers`)
- `smp:sourceFolders` paths match actual archive contents
- Tile paths in `tiles` URL template resolve to actual entries
- No unexpected files (e.g., `_cache_meta.json`)

### Verification

- Run full test suite: `PYTHONPATH=. python3 test/test_generator.py`
- If using Node.js validator: `npx smp validate test_output.smp`
- Should pass after Tasks 1-4 are complete

---

## Reference: Key Upstream Files

| File | Purpose |
|------|---------|
| `../styled-map-package/spec/1.0/README.md` | SMP 1.0 format specification |
| `../styled-map-package/packages/api/lib/writer.js` | Reference SMP writer (writes VERSION, sorts entries, dedup) |
| `../styled-map-package/packages/api/lib/reader.js` | Reference SMP reader (fallback defaults, ZIP mode handling) |
| `../styled-map-package/packages/api/lib/validator.js` | SMP format validator (checks VERSION, sourceFolders, tiles) |

## Recommended Execution Order

1. Task 1 (VERSION file) — trivial, unblocks Task 7
2. Task 2 (sourceFolders fix) — trivial
3. Task 3 (remove bogus center) — trivial
4. Task 4 (ZIP_STORED for tiles) — easy-medium
5. Task 5 (WebP support) — medium, optional feature
6. Task 6 (deduplication) — medium-hard, performance optimization
7. Task 7 (validator integration) — medium, best done after 1-4
