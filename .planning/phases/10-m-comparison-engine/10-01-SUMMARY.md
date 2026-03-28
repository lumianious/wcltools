---
phase: 10-m-comparison-engine
plan: 01
subsystem: api
tags: [pydantic, comparison, mplus, gap-analysis]

# Dependency graph
requires:
  - phase: 09-m-benchmark-aggregation
    provides: SegmentDamageBreakdown, MplusBenchmarkSegment models and benchmark data
provides:
  - SegmentDamageGap, SegmentComparison, BossCastComparison, DeathBreakdown, MplusComparisonResponse Pydantic models
  - _compare_trash_damage function for per-spell damage gap analysis
  - _compare_interrupts function for interrupt count and target comparison
  - _compute_gap utility for 20% threshold flagging
affects: [10-02-PLAN, 10-03-PLAN, mplus_comparison]

# Tech tracking
tech-stack:
  added: []
  patterns: [gap-percentage-flagging, spell-id-matching, set-diff-for-critical-misses]

key-files:
  created:
    - src/tools/mplus_comparison.py
    - tests/test_mplus_comparison.py
  modified:
    - src/models.py
    - src/tools/CLAUDE.md
    - tests/CLAUDE.md

key-decisions:
  - "gap_pct = bench_pct - player_pct (direct difference for damage %), _compute_gap for ratio-based gaps"
  - "Benchmark-only spells flagged only when bench_pct > 5% to avoid noise from minor abilities"

patterns-established:
  - "Damage gap: direct pct difference (bench_pct - player_pct), flagged > 20.0"
  - "Interrupt gap: ratio-based via _compute_gap, critical missed = set difference"

requirements-completed: [DMG-02, INT-02]

# Metrics
duration: 3min
completed: 2026-03-28
---

# Phase 10 Plan 01: Comparison Models + Trash Damage & Interrupt Comparison Summary

**Pydantic models for M+ comparison results plus trash damage gap analysis (DMG-02) and interrupt comparison with critical missed target detection (INT-02)**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T16:25:12Z
- **Completed:** 2026-03-28T16:28:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- 5 new Pydantic models for the full M+ comparison response structure (SegmentDamageGap, SegmentComparison, BossCastComparison, DeathBreakdown, MplusComparisonResponse)
- Per-spell trash damage comparison with 20% threshold flagging and benchmark-only spell detection
- Interrupt comparison with count gap analysis and critical missed target identification via set difference
- 6 new tests all passing, 679 total tests with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Pydantic models + test scaffold with failing tests** - `3a8a0bf` (test)
2. **Task 2: Implement trash damage comparison and interrupt comparison** - `70b1b80` (feat)

## Files Created/Modified
- `src/models.py` - Added 5 Phase 10 Pydantic models for comparison results
- `src/tools/mplus_comparison.py` - New module with _compute_gap, _compare_trash_damage, _compare_interrupts
- `tests/test_mplus_comparison.py` - 6 tests covering DMG-02 and INT-02 requirements
- `src/tools/CLAUDE.md` - Updated L2 member list with mplus_comparison.py
- `tests/CLAUDE.md` - Updated L2 member list with test_mplus_comparison.py

## Decisions Made
- gap_pct for damage uses direct percentage difference (bench_pct - player_pct) rather than ratio, since damage_pct values are already normalized
- Benchmark-only spells only flagged when bench_pct > 5.0% to filter noise from minor/proc abilities
- _compute_gap uses ratio formula for interrupt counts where raw values need normalization

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functions are fully implemented with no placeholder data.

## Next Phase Readiness
- Models ready for Plan 02 (boss cast comparison, death analysis)
- _compare_trash_damage and _compare_interrupts ready for integration into full comparison pipeline
- _compute_gap utility available for reuse in CD and defensive comparisons

---
*Phase: 10-m-comparison-engine*
*Completed: 2026-03-28*
