---
phase: 11-m-coaching-tool
plan: 01
subsystem: api
tags: [pydantic, coaching, mplus, nlp]

requires:
  - phase: 10-m-comparison-engine
    provides: MplusComparisonResponse with segment/boss/death comparisons
provides:
  - CoachingItem, SegmentCoaching, DungeonCoachingSummary, MplusCoachingResponse Pydantic models
  - Pure-function coaching transformation layer (no API calls)
  - coach_mplus_run async entry point
affects: [11-02 MCP tool registration, server.py]

tech-stack:
  added: []
  patterns: [dual structured+NL coaching output, top-N priority ranking by gap_pct]

key-files:
  created: [src/tools/mplus_coaching.py, tests/test_mplus_coaching.py]
  modified: [src/models.py, src/tools/CLAUDE.md, tests/CLAUDE.md]

key-decisions:
  - "Coaching items sorted by gap_pct descending for impact-based priority"
  - "Positive feedback category for segments meeting benchmark"
  - "_build_coaching_response as testable pure function separate from async coach_mplus_run"

patterns-established:
  - "Coaching transformation: comparison data -> CoachingItem list with category + NL text"
  - "Top-3 per segment, top-5 overall improvement cap"

requirements-completed: [COACH-01, COACH-02, COACH-03]

duration: 3min
completed: 2026-03-28
---

# Phase 11 Plan 01: M+ Coaching Transformation Layer Summary

**Pure-function coaching logic converting MplusComparisonResponse into prioritized CoachingItems with dual structured+NL format, top 3 per segment, top 5 overall**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-28T17:25:07Z
- **Completed:** 2026-03-28T17:29:06Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 5

## Accomplishments
- Coaching Pydantic models (CoachingItem, SegmentCoaching, DungeonCoachingSummary, MplusCoachingResponse)
- Per-trash coaching: top 3 damage/CD/interrupt gaps sorted by impact with NL advice
- Per-boss coaching: top 3 cast/CD issues with NL advice
- Death coaching: per-death NL with defensive availability info
- Dungeon summary: flag counts + top 5 improvements + overall NL text
- Positive feedback for segments meeting benchmark
- 9 unit tests covering all coaching behaviors, 699 total tests green

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests** - `e8213f8` (test)
2. **Task 1 (GREEN): Implementation** - `3a9c960` (feat)

**Plan metadata:** [pending] (docs: complete plan)

_Note: TDD task with RED + GREEN commits_

## Files Created/Modified
- `src/models.py` - Added 4 coaching Pydantic models (CoachingItem, SegmentCoaching, DungeonCoachingSummary, MplusCoachingResponse)
- `src/tools/mplus_coaching.py` - Coaching transformation functions + coach_mplus_run entry point
- `tests/test_mplus_coaching.py` - 9 unit tests covering all coaching behaviors
- `src/tools/CLAUDE.md` - Added mplus_coaching.py entry + dependency
- `tests/CLAUDE.md` - Added test_mplus_coaching.py entry

## Decisions Made
- Coaching items sorted by gap_pct descending for impact-based priority ranking
- Positive feedback (category="positive") returned for segments with no flagged gaps
- _build_coaching_response extracted as testable pure function, separate from async coach_mplus_run
- Death coaching includes defensive availability status (available_never_used vs on_cooldown)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functions produce real coaching output from comparison data.

## Next Phase Readiness
- Coaching transformation layer complete, ready for Plan 02 (MCP tool registration in server.py)
- coach_mplus_run is the async entry point for the MCP tool

---
*Phase: 11-m-coaching-tool*
*Completed: 2026-03-28*
