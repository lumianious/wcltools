---
phase: 10-m-comparison-engine
plan: 02
subsystem: api
tags: [comparison, mplus, boss-analysis, death-analysis, defensive-availability]

# Dependency graph
requires:
  - phase: 10-m-comparison-engine/plan-01
    provides: SegmentDamageGap, BossCastComparison, DeathBreakdown Pydantic models, _compute_gap utility
  - phase: 09-m-benchmark-aggregation
    provides: _extract_segment_cds, _query_segment_cast_events, _build_tracked_spells
provides:
  - _compare_boss_casts function for per-spell boss cast gap analysis
  - _compare_boss_cds function for CD missed uses detection
  - _check_defensive_availability function for three-state defensive status classification
  - _build_death_breakdown function for per-death damage sources + defensive status
  - _query_damage_taken_events async function for WCL DamageTaken queries
affects: [10-03-PLAN, mplus_comparison]

# Tech tracking
tech-stack:
  added: []
  patterns: [boss-cast-gap-flagging, defensive-availability-classification, damage-taken-targetID-query]

key-files:
  created: []
  modified:
    - src/tools/mplus_comparison.py
    - tests/test_mplus_comparison.py
    - src/tools/CLAUDE.md

key-decisions:
  - "Boss cast gap uses _compute_gap ratio (same 20% threshold as other comparisons)"
  - "Expected CD casts = 1 + floor((duration - 1) / cd_seconds) — assumes first cast at pull"
  - "Defensive availability: three-state classification (never_used, on_cooldown, off_cooldown)"
  - "DamageTaken query uses targetID (not sourceID) per WCL API semantics"

patterns-established:
  - "Boss comparison: per-spell cast count gap with flagging > 20%"
  - "Death analysis: damage sources grouped by abilityGameID, sorted desc, top 5"
  - "Defensive check: last_cast_ts + cd_ms vs death_ts for cooldown state"

requirements-completed: [BOSS-01, BOSS-02, SURV-02]

# Metrics
duration: 3min
completed: 2026-03-28
---

# Phase 10 Plan 02: Boss Comparison + Death Analysis Summary

**Boss cast-by-cast comparison with CD gap detection plus per-death damage breakdown with three-state defensive availability classification**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T16:29:37Z
- **Completed:** 2026-03-28T16:32:45Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Boss cast comparison produces per-spell cast count gaps against benchmark with 20% threshold flagging
- Boss CD comparison detects missed uses based on fight duration / cooldown seconds
- Death analysis classifies defensive availability into three states: available_never_used, on_cooldown, available_off_cooldown
- DamageTaken query uses targetID (not sourceID) per WCL API semantics
- 7 new tests, 686 total tests with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add failing tests for boss comparison and death analysis** - `47f10ae` (test)
2. **Task 2: Implement boss comparison and death analysis functions** - `d0763fe` (feat)

## Files Created/Modified
- `src/tools/mplus_comparison.py` - Added _compare_boss_casts, _compare_boss_cds, _check_defensive_availability, _build_death_breakdown, _query_damage_taken_events
- `tests/test_mplus_comparison.py` - Added TestBossComparison (3 tests) and TestDeathAnalysis (4 tests)
- `src/tools/CLAUDE.md` - Updated mplus_comparison.py interface list

## Decisions Made
- Boss cast gap uses existing _compute_gap ratio (consistent 20% threshold across all comparison types)
- Expected CD casts formula: 1 + floor((duration - 1) / cd_seconds) — assumes first cast at pull
- Defensive availability uses millisecond math: last_cast_ts + cd_ms > death_ts = on_cooldown
- DamageTaken events grouped by abilityGameID with amounts summed, sorted descending, top 5

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functions are fully implemented with no placeholder data.

## Next Phase Readiness
- Boss comparison and death analysis functions ready for integration into full compare_mplus_run pipeline (Plan 03)
- All comparison primitives complete: trash damage, interrupts, boss casts, boss CDs, death breakdown
- _query_damage_taken_events ready for async pipeline orchestration

---
*Phase: 10-m-comparison-engine*
*Completed: 2026-03-28*
