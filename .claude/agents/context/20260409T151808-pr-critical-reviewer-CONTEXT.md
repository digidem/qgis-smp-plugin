---
agent: pr-critical-reviewer
timestamp: 2026-04-09T15:18:08Z
prior_context: []
next_agents: [pr-code-fixer]
---

## Mission Summary
**PR Reviewed:** working tree / publication-readiness follow-up changes
**Review Status:** ⚠️ Issues Found
**Critical Issues:** 1

## Critical Issues (if any)
1. **[Correctness]** - `comapeo_smp_generator.py:410-422` - `world_pct` now uses only `sources[0]['total_tiles']` as the numerator, so world-enabled exports under-report coverage whenever region-detail tiles add unique world coverage (for a full-world export at z6-z7 it reports `85/20565 = 0.41%` instead of `100%`) → user-facing coverage estimates and any caller of `estimate_world_pyramid_percentage()` become wrong → Fix required: compute a deduplicated union-of-covered-tiles numerator across all exported sources/zooms instead of hard-coding the world source only. The current tests also lock in the broken behavior rather than the API contract: `comapeo_smp_generator.py:476-487`, `comapeo_smp_algorithm.py:347-356`, `test/test_generator.py:3576-3689`.

## Handoff for PR Code Fixer (per issue)
Issue #1:
- File: `comapeo_smp_generator.py` Line: 410
- Problem: `world_coverage_tiles` is derived from only the low-zoom world source, which breaks the documented meaning of `estimate_world_pyramid_percentage()` and produces bogus UI output for large/full-world exports with world-base zooms enabled.
- Fix guidance: Replace the numerator with the deduplicated set of world tiles covered by the complete export plan (all sources, all export zooms, with overlapping low zooms counted once). Keep `total_tiles` unchanged for sizing. Update the Processing log message expectations accordingly.
- Test requirement: Add a regression test where `include_world_base_zooms=True` and the export extent is the full world at higher zooms; assert `world_pct == 100.0` and the displayed numerator equals `world_tiles`, not `85`.
