---
agent: pr-critical-reviewer
timestamp: 2026-04-22T01:06:29Z
prior_context: [20260409T151808-pr-critical-reviewer-CONTEXT.md, 20260410T002558-pr-critical-reviewer-CONTEXT.md, 20260421T074002-pr-critical-reviewer-CONTEXT.md]
next_agents: [pr-code-fixer]
---

## Mission Summary
**PR Reviewed:** working tree / fixed 3-slot world-region-local refactor
**Review Status:** ⚠️ Issues Found
**Critical Issues:** 2

## Critical Issues (if any)
1. **[Correctness]** - `comapeo_smp_generator.py:594-600` / `comapeo_smp_generator.py:1098-1139` - Local-only exports now hard-code `source_id="local-detail"` on slot `2` and write style metadata/URLs to `s/2` instead of the legacy single-source contract (`mbtiles-source` on `s/0`) → existing single-source SMP workflows/fixtures in this repo still target the old contract (`generate_smp_from_xyz.sh:67-102`, `test/style_example.json:5-58`, `XYZ_SMP.md:137-177`) so automation and downstream tooling will break on upgrade → Fix required.
2. **[Correctness]** - `comapeo_smp_algorithm.py:160-178` - `INCLUDE_WORLD_BASE_ZOOMS` default flipped from `True` to `False` → existing Processing runs/scripts that rely on stable parameter IDs but omit this field will silently lose low-zoom world coverage, producing materially different archives and blank low zoom levels relative to previous releases → Fix required.

## Handoff for PR Code Fixer (per issue)
Issue #1:
- File: `comapeo_smp_generator.py` Line: 594
- Problem: Single-source export contract changed from legacy `mbtiles-source`/`s/0` to sparse-slot `local-detail`/`s/2` without compatibility handling.
- Fix guidance: Preserve legacy single-source IDs/folder layout for local-only exports, or add a compatibility alias/migration layer and update archive/style generation consistently.
- Test requirement: A local-only export must remain consumable by existing single-source workflows; add regression coverage against legacy style/archive expectations.

Issue #2:
- File: `comapeo_smp_algorithm.py` Line: 160
- Problem: World overview became opt-in by default, silently changing existing Processing behavior.
- Fix guidance: Restore the previous default or gate the new behavior behind an explicit migration/versioned parameter change.
- Test requirement: Invoking the algorithm without explicitly setting `INCLUDE_WORLD_BASE_ZOOMS` must preserve previous low-zoom coverage behavior.
