---
phase: 08-m-api-foundation
verified: 2026-03-28T00:00:00Z
status: gaps_found
score: 4/5 must-haves verified
gaps:
  - truth: "Dungeon run queries include keystoneLevel and keystoneBonus fields"
    status: failed
    reason: "Plan 08-02 SUMMARY documents this as a deliberate skip — dungeon_analysis.py _query_all_fights GraphQL does not contain keystoneLevel or keystoneBonus. The SUMMARY records this as deviation with impact: 'Add keystone fields when merging with main branch or in a follow-up task.'"
    artifacts:
      - path: "src/tools/dungeon_analysis.py"
        issue: "keystoneLevel, keystoneBonus, keystoneAffixes, keystoneTime fields missing from _query_all_fights GraphQL query"
    missing:
      - "Add keystoneLevel, keystoneBonus, keystoneAffixes, keystoneTime to _query_all_fights GraphQL in src/tools/dungeon_analysis.py"
      - "Add unit test verifying _query_all_fights query string contains keystoneLevel"
human_verification:
  - test: "Run scripts/verify_mplus_api.py with live WCL credentials"
    expected: "Step 1 returns M+ encounter IDs, Step 2 confirms difficulty=10 returns rankings with bracketData as keystone level, Step 3 confirms bracket=12 filtering, Step 4 confirms keystoneLevel field availability"
    why_human: "Requires live WCL API credentials — automated checks cannot verify live API behavior"
---

# Phase 8: M+ API Foundation Verification Report

**Phase Goal:** Agent can query WCL for M+ ranking and report data with verified API parameters
**Verified:** 2026-03-28
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Agent can query WCL M+ leaderboard for top players in a specific dungeon+spec+key level | VERIFIED | `query_mplus_rankings` in `src/tools/mplus_rankings.py` queries `characterRankings` with `difficulty: 10` and optional `bracket` param; 11 tests cover the full query path |
| 2 | Agent can filter M+ rankings by keystone level bracket without cross-bracket contamination | VERIFIED | `_query_rankings_raw` passes `bracket: {key_level}` when provided, omits when None; test `test_bracket_in_query_when_provided` and `test_no_bracket_when_none` pass |
| 3 | M+ benchmark data is cached per dungeon+spec+key level combination with 6h TTL | VERIFIED | Cache key `mplus_bench:{spec}:{encounter_id}:k{key_level}`, `CACHE_TTL_SECONDS = 6 * 3600`; `test_cache_hit` and `test_cache_key_format` pass |
| 4 | Dungeon run queries include keystoneLevel and keystoneBonus fields | FAILED | `_query_all_fights` in `src/tools/dungeon_analysis.py` does NOT contain keystoneLevel/keystoneBonus — explicitly skipped in Plan 08-02 SUMMARY as a deviation |
| 5 | Sparse bracket (< 3 results) triggers adjacent bracket fallback with disclosure | VERIFIED | Fallback logic tries +1 then -1; `actual_bracket` tracked in `MplusBenchmarkMeta`; `test_sparse_bracket_fallback` and `test_sparse_fallback_discloses_actual_bracket` pass |

**Score:** 4/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/tools/builds.py` | DIFFICULTY_MAP with "mythic_plus": 10 | VERIFIED | Line 53: `"mythic_plus": 10  # M+ / Challenge Mode` |
| `src/models.py` | MplusRankingEntry and MplusBenchmarkMeta models | VERIFIED | Lines 692-726 — both classes present with correct fields and aliases |
| `src/tools/mplus_rankings.py` | M+ rankings query with bracket filtering and cache | VERIFIED | 253 lines, fully implemented, exports `query_mplus_rankings` |
| `src/tools/dungeon_analysis.py` | keystoneLevel/keystoneBonus in fight queries | STUB/MISSING | `_query_all_fights` GraphQL at lines 80-95 missing all 4 keystone fields |
| `tests/test_mplus_foundation.py` | Unit tests for all M+ foundation changes | VERIFIED | 467 lines, 23 tests, all passing |
| `scripts/verify_mplus_api.py` | Live WCL M+ API parameter verification | VERIFIED | 404 lines, 4 verification steps present |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/tools/mplus_rankings.py` | `src/tools/builds.py` | imports SPEC_MAPPING, DIFFICULTY_MAP | WIRED | Line 24: `from src.tools.builds import SPEC_MAPPING` |
| `src/tools/mplus_rankings.py` | `src/cache.py` | cache_get/cache_set with mplus_bench: key | WIRED | Lines 22, 180-181, 251: `cache_get`/`cache_set` with `mplus_bench:` prefix |
| `src/tools/mplus_rankings.py` | `src/models.py` | returns MplusRankingEntry list | WIRED | Line 23: `from src.models import MplusBenchmarkMeta, MplusRankingEntry`; used throughout |
| `tests/test_mplus_foundation.py` | `src/tools/mplus_rankings.py` | tests query_mplus_rankings | WIRED | 11 test methods import and call `query_mplus_rankings` |
| `scripts/verify_mplus_api.py` | `src/wcl_client.WCLClient` | direct import and query | WIRED | Line 31: `from src.wcl_client import WCLClient` |
| `scripts/verify_mplus_api.py` | `src/tools/encounters.py` | get_encounters call | WIRED | Line 32: `from src.tools.encounters import get_encounters`; called with `content_type="mythic_plus"` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `src/tools/mplus_rankings.py` | `rankings` | `_query_rankings_raw` -> `client.query(gql)` | Yes — live WCL GraphQL query with `characterRankings` | FLOWING |
| `src/tools/mplus_rankings.py` | `cached` | `cache_get(cache_key, CACHE_TTL_SECONDS)` | Yes — populated by prior `cache_set` call | FLOWING |
| `src/tools/mplus_rankings.py` | `entries` | `_parse_rankings(rankings, sample_size)` | Yes — parsed from real rankings data | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `test_mplus_foundation.py` 23 tests pass | `.venv/bin/python -m pytest tests/test_mplus_foundation.py -v` | 23 passed, 0 failed | PASS |
| Full test suite (657 tests) — no regression | `.venv/bin/python -m pytest tests/ -x -q` | 657 passed, 0 failed | PASS |
| DIFFICULTY_MAP has mythic_plus=10 | `grep -c '"mythic_plus": 10' src/tools/builds.py` | 1 | PASS |
| query_mplus_rankings function present | `grep -c 'async def query_mplus_rankings' src/tools/mplus_rankings.py` | 1 | PASS |
| keystoneLevel in dungeon_analysis.py | `grep -c 'keystoneLevel' src/tools/dungeon_analysis.py` | 0 | FAIL |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| BENCH-01 | 08-01, 08-02 | Agent can query WCL M+ leaderboard for top DPS players in a specific dungeon+spec+key level | SATISFIED | `query_mplus_rankings` queries `characterRankings` with `difficulty: 10` + `bracket` + spec filtering; 08-01 verified parameters live |
| BENCH-04 | 08-02 | Benchmark data is cached per dungeon+spec+key level combination | SATISFIED | Cache key `mplus_bench:{spec}:{encounter_id}:k{key_level}`, 6h TTL (`CACHE_TTL_SECONDS = 21600`), verified by `test_cache_key_format` and `test_cache_hit` |

Both requirements marked complete in REQUIREMENTS.md traceability table. Coverage confirmed.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/tools/dungeon_analysis.py` | 80-95 | Missing keystoneLevel/keystoneBonus fields in `_query_all_fights` GraphQL | Warning | Phase 9 code that reads aggregate fight keystone data from dungeon reports will get null values; not a current blocker since no consumer code exists yet |

No TODO/FIXME/placeholder comments found in any Phase 8 files. No stub implementations. No hardcoded empty returns.

### Human Verification Required

#### 1. Live API Parameter Confirmation

**Test:** Run `scripts/verify_mplus_api.py` with valid `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET`
**Expected:** Step 1 discovers M+ encounters in Zone 47; Step 2 confirms `difficulty=10` returns rankings with `bracketData` as keystone level integer; Step 3 confirms `bracket=12` filters; Step 4 confirms `keystoneLevel` field in aggregate fights
**Why human:** Requires live WCL OAuth credentials. 08-01 SUMMARY documents this was already completed by human on 2026-03-28 with all 4 steps confirmed, but automated verification cannot replicate this.

### Gaps Summary

One gap blocks full phase completion: the `_query_all_fights` function in `src/tools/dungeon_analysis.py` was not updated to include `keystoneLevel`, `keystoneBonus`, `keystoneAffixes`, and `keystoneTime` fields. This was explicitly documented as a deliberate deviation in Plan 08-02 SUMMARY — the executor noted `dungeon_analysis.py` was not available in the parallel execution branch at the time.

This gap does not prevent BENCH-01 or BENCH-04 from being satisfied (both are about rankings queries and caching, which are fully functional). However, the Plan 08-02 `must_haves` truth "Dungeon run queries include keystoneLevel and keystoneBonus fields" is unmet, making the plan's own acceptance criteria incomplete.

The fix is mechanical: add 4 fields to one GraphQL f-string in `dungeon_analysis.py` and add one test asserting the query string contains `keystoneLevel`. No architectural changes needed.

---

_Verified: 2026-03-28_
_Verifier: Claude (gsd-verifier)_
