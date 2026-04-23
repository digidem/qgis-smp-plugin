---
agent: pr-critical-reviewer
timestamp: 2026-04-21T07:40:02+00:00
prior_context: [20260409T151808-pr-critical-reviewer-CONTEXT.md, 20260410T002558-pr-critical-reviewer-CONTEXT.md]
next_agents: [pr-code-fixer]
---

## Mission Summary
**PR Reviewed:** Readiness assessment for auto-detected zoom-dependent multi-source support
**Review Status:** ⚠️ Issues Found
**Critical Issues:** 5

## Critical Issues (if any)
1. **[Correctness]** - `comapeo_smp_generator.py:829-1006` - Style generation only supports exactly 1 or 2 sources; any future auto-detected plan with 3+ sources will fall back to a single `mbtiles-source` style pointing at `s/0` while tiles for other sources are still packaged → rendered output and archive metadata diverge → Fix required.
2. **[Correctness]** - `comapeo_smp_generator.py:1129-1172` - Tile rendering is not source-aware and always uses the same layer stack from `map_settings_template.layers()` → zoom-group sources would render identical content unless per-source layer selection is threaded through → Fix required.
3. **[Correctness]** - `comapeo_smp_generator.py:1091-1102` - Cache fingerprints only include project/layer identity, not detected zoom-group rendering config → cache/resume can incorrectly reuse stale tiles after breakpoint/source-plan changes → Fix required.
4. **[Correctness]** - `research-zoom-sources.md:44-53` / `comapeo_smp_generator.py:1167-1172` - Detection relies on a computed zoom→scale mapping that is not yet validated against actual QGIS rendering scale decisions → off-by-one breakpoint errors are likely for scale-based visibility/labels → Fix required.
5. **[Correctness]** - `research-zoom-sources.md:167-178` - Continuous data-defined overrides (`@map_scale`, `@zoom_level`) are explicitly out of scope, but there is no current fallback/warning/test strategy to prevent mis-detection or source explosion → Fix required.

## Handoff for PR Code Fixer (per issue)
Issue #1:
- File: `comapeo_smp_generator.py` Line: 829
- Problem: Multi-source style generation is hard-coded to exactly two sources.
- Fix guidance: Generalize source/layer/style metadata loops for N sources and add tests for 3+ sources plus world+detected-group combinations.
- Test requirement: A 3-source plan must produce 3 style sources, 3 raster layers, and matching `smp:sourceFolders` entries.

Issue #2:
- File: `comapeo_smp_generator.py` Line: 1129
- Problem: Rendering ignores source-specific layer composition.
- Fix guidance: Thread per-source/per-group render configs into tile scheduling and `_render_single_tile`, preserving layer order.
- Test requirement: Different source groups must render different selected layer sets without changing order.

Issue #3:
- File: `comapeo_smp_generator.py` Line: 1091
- Problem: Cache fingerprint omits detected source/rendering configuration.
- Fix guidance: Include a stable serialization of the export/render plan (or per-source layer signature) in fingerprints.
- Test requirement: Changing only breakpoint grouping or source layer membership must invalidate cached tiles.

Issue #4:
- File: `research-zoom-sources.md` Line: 44
- Problem: Zoom breakpoint detection is only theoretical and not validated against QGIS runtime behavior.
- Fix guidance: Add headless QGIS integration coverage for layer visibility, rule-based symbology, and labeling scale transitions before relying on detection.
- Test requirement: Real QGIS tests must assert detected breakpoints match rendered output at boundary zooms.

Issue #5:
- File: `research-zoom-sources.md` Line: 167
- Problem: Unsupported continuous data-defined scale expressions have no fail-safe behavior defined.
- Fix guidance: Detect unsupported cases and warn/fallback to single-source export instead of auto-splitting.
- Test requirement: Projects using `@map_scale`/`@zoom_level` expressions must not silently generate incorrect source plans.
