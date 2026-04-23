---
agent: pr-critical-reviewer
timestamp: 2026-04-22T05:31:17Z
prior_context: [20260422T010629-pr-critical-reviewer-CONTEXT.md, 20260421T074002-pr-critical-reviewer-CONTEXT.md]
next_agents: [pr-code-fixer]
---

## Mission Summary
**PR Reviewed:** #13 feature: implement fixed world-region-local SMP sources
**Review Status:** ⚠️ Issues Found
**Critical Issues:** 2

## Critical Issues (if any)
1. **[Correctness]** - `comapeo_smp_generator.py:610-615` / `comapeo_smp_generator.py:1114-1155` - Local-only exports now always emit `local-detail` on slot `s/2` instead of the legacy single-source contract (`mbtiles-source` on `s/0`) → existing single-source consumers and repo fixtures/scripts (`generate_smp_from_xyz.sh:34-52`, `generate_smp_from_xyz.sh:66-102`, `test/style_example.json:5-58`) must all change in lockstep, so upgrades break established archive/style expectations → Fix required.
2. **[Correctness]** - `comapeo_smp_algorithm.py:160-178` - `INCLUDE_WORLD_BASE_ZOOMS` default changed from `True` to `False` while keeping the same Processing parameter ID → existing scripted/automated runs that omit the parameter silently lose low-zoom world coverage and can ship blank zoomed-out maps → Fix required.

## Handoff for PR Code Fixer (per issue)
Issue #1:
- File: `comapeo_smp_generator.py` Line: 610
- Problem: Single-source exports changed the stable style/source/folder contract from `mbtiles-source`/`s/0` to `local-detail`/`s/2`.
- Fix guidance: Preserve legacy single-source layout for local-only exports, or add an explicit compatibility layer/migration path instead of silently changing the contract.
- Test requirement: A local-only export must still generate the legacy style/archive shape expected by pre-existing single-source workflows.

Issue #2:
- File: `comapeo_smp_algorithm.py` Line: 160
- Problem: Existing Processing callers now get World disabled by default without changing parameter IDs.
- Fix guidance: Restore the previous default or version/gate the behavior change so older callers keep the same output.
- Test requirement: Running the algorithm without explicitly setting `INCLUDE_WORLD_BASE_ZOOMS` must preserve prior low-zoom coverage behavior.
