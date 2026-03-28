# Project Research Summary

**Project:** WoW Coach MCP Server — v2.0 M+ Coaching Intelligence Milestone
**Domain:** Mythic+ dungeon coaching via WarcraftLogs API
**Researched:** 2026-03-28
**Confidence:** MEDIUM (architecture and feature set HIGH; specific WCL M+ API parameters MEDIUM — need live verification)

## Executive Summary

This milestone adds Mythic+ dungeon coaching to an existing, well-structured raid coaching MCP server. The core approach is to reuse the existing raid pipeline (rankings -> reports -> events -> aggregate -> cache) with one key adaptation: M+ dungeons use `difficulty: 10` and dungeon-level encounter IDs, whereas raids use `difficulty: 3/4/5` and boss encounter IDs. No new dependencies are required. The entire milestone is additive: new tool functions, one new constant in `DIFFICULTY_MAP`, a thin benchmark orchestrator, a comparison engine, and a top-level coaching tool.

The recommended approach is a four-phase build: (1) foundation — add M+ difficulty support and verify API parameters; (2) benchmark aggregation — build `get_mplus_benchmarks` as a cached, per-dungeon benchmark bundle using existing raid benchmark tools with `difficulty="mythic_plus"`; (3) per-segment comparison and death analysis — the new logic that M+ requires beyond raid patterns; (4) a coaching tool `coach_dungeon_run` that orchestrates all prior phases into actionable coaching output. The existing `dungeon_analysis.py` patterns (active-time DPS, segment classification, parallel queries) are already solid and should be preserved and extended.

The primary risks are WCL M+ API parameter uncertainty (`difficulty: 10` and `bracket` for keystone level need live verification), rate-limit exhaustion if benchmarks are built eagerly across all 8 dungeons, and benchmark contamination from comparing incompatible key levels. All three risks have clear mitigations: verify API parameters in Phase 1 before building any tooling, build benchmarks lazily per-dungeon, and always filter rankings by bracket (keystone level).

---

## Key Findings

### Recommended Stack

No new dependencies. The existing Python 3.12 / httpx / Pydantic v2 / mcp SDK stack handles everything. The only change required is adding `"mythic_plus": 10` to `DIFFICULTY_MAP` in `builds.py` and creating three new source files (`_mplus_benchmarks.py`, `_mplus_comparisons.py`, `mplus_coach.py`). All existing infrastructure — `WCLClient`, file-based JSON cache, `asyncio.gather` patterns, spell/talent data — transfers directly.

**Core technologies (unchanged):**
- Python 3.12+ — runtime, no changes needed
- httpx — WCL GraphQL transport, no changes needed
- Pydantic v2 — extend with new M+ models (`MplusBenchmarkBundle`, `MplusCoachingResponse`, `SegmentAnalysis`)
- mcp SDK — register two new tools (`get_mplus_benchmarks`, `coach_dungeon_run`) in `server.py`

**New GraphQL pattern (one only):**
- `characterRankings(difficulty: 10, bracket: <key_level>)` — the only new query shape; all downstream event/buff/damage queries are identical to raid patterns

### Expected Features

**Must have (table stakes):**
- M+ benchmark aggregation (rotation profile, cooldown timelines, defensive patterns per dungeon) — without this, coaching is purely descriptive, not comparative
- M+ cooldown timeline across the full dungeon run — CD spacing relative to pull boundaries is the single highest-value M+ coaching signal
- M+ rotation profile with dungeon-specific CPM/buff baselines — AoE vs ST spell distribution differs meaningfully from raid
- M+ per-segment gap analysis — identify which trash packs and bosses caused DPS loss
- M+ death analysis with defensive availability check — deaths deplete keys; every death costs key time

**Should have (competitive differentiators):**
- Cooldown-to-pull mapping — "you used X on a 3-mob pack; top players save it for the 8-mob pull"
- CD waste detection — detect gaps between CD uses that exceed the spell cooldown duration
- Pull-by-pull DPS curve — per-segment DPS is already available; structuring as a sequence is low lift, high coaching value
- Key level scaling context — normalize benchmarks to bracket; present "expected DPS at this key level"
- Affix-aware coaching — pass affix context to Claude; primarily prompt enrichment, not data processing

**Defer to v2.1+:**
- Interrupt analysis — high value but requires separate event type queries; adds budget complexity
- Cross-dungeon meta analysis — multi-report orchestration; scope creep for this milestone
- Route optimization / MDT integration — fundamentally different data pipeline, not WCL-based
- Group composition analysis — 5x API budget, different coaching model

### Architecture Approach

The architecture follows a strict reuse-and-extend principle: the existing raid pipeline becomes reusable for M+ by parameterizing on `difficulty="mythic_plus"`. Three new focused components are added — a benchmark orchestrator (`_mplus_benchmarks.py`) that calls existing raid benchmark tools in parallel, a comparison engine (`_mplus_comparisons.py`) that reuses `_analysis_comparisons.py` functions for full-run analysis and adds new segment/death/CD-spacing logic, and a coaching tool (`mplus_coach.py`) that orchestrates a two-phase flow (identify dungeon -> parallel fetch player data + benchmarks -> compare -> output).

**Major components:**
1. `builds.py` (MODIFY) — add `"mythic_plus": 10` to `DIFFICULTY_MAP`; unlocks all existing benchmark tools for M+
2. `_mplus_benchmarks.py` (NEW) — thin orchestrator: `asyncio.gather(rotation, timelines, defensives)` with `difficulty="mythic_plus"`, returns `MplusBenchmarkBundle`, cached 6h
3. `_mplus_comparisons.py` (NEW) — full-run comparison (delegates to existing functions) + segment analysis + death correlation + CD waste detection (new logic)
4. `mplus_coach.py` (NEW) — top-level `coach_dungeon_run` tool; two-phase orchestration (identify -> parallel fetch -> compare)
5. `dungeon_analysis.py` (MODIFY) — add `keystoneBonus`/`keystoneLevel` to fight queries for run quality detection
6. `models.py` (MODIFY) — add `MplusBenchmarkBundle`, `MplusCoachingResponse`, `SegmentAnalysis`
7. `server.py` (MODIFY) — register `get_mplus_benchmarks` and `coach_dungeon_run`

**Tool structure (three tools, not one monolithic tool):**
- `analyze_dungeon_run` (existing, minor enhancements) — quick data overview, ~5-7 pts
- `get_mplus_benchmarks` (NEW) — cached benchmark bundle per dungeon, ~200 pts first call / 0 pts cached
- `coach_dungeon_run` (NEW) — full coaching session, ~50-100 pts + benchmark cost if uncached

### Critical Pitfalls

1. **WCL M+ API structure differs from raid** — `difficulty: 10` and dungeon encounter IDs behave differently; `bracketData` is keystone level not item level; `fightID` from M+ rankings points to aggregate dungeon fight. Mitigation: live API verification in Phase 1 before building any tooling; verify with GraphQL explorer.

2. **Rate limit exhaustion from eager benchmark building** — 8 dungeons x full benchmark = 160-720 points; with deep cast queries, could approach the 3600 pt/hour budget in a single session. Mitigation: build benchmarks lazily (per-dungeon, on-demand), `sample_size=5` for M+ (vs raid's 50), cache 6h, prefer `table()` aggregates over `events()` pagination for basic benchmarks.

3. **Cross-key-level benchmark contamination** — top WCL rankings skew toward +14/+15 keys; comparing a +8 player to +14 benchmarks produces meaningless and demoralizing coaching. Mitigation: always pass `bracket` parameter to filter rankings by keystone level; if bracket data is sparse, explicitly disclose bracket gap in coaching output.

4. **M+ data quality — depleted keys and partial logs** — WCL rankings are pre-filtered for timed runs, but player log analysis may encounter depleted/partial runs. The existing codebase does not query `keystoneBonus` or `keystoneLevel`. Mitigation: add these fields to `_query_all_fights`; add `run_quality` classification ("timed"/"depleted"/"partial") to analysis output.

5. **Benchmark staleness — M+ meta shifts faster than raid** — affix rotations, weekly hotfixes, and gear inflation make M+ benchmarks stale faster than raid benchmarks. Mitigation: include affix set in cache key; expose `benchmark_age` in coaching output; consider 2-4h TTL early in season, relaxing to 6-12h after meta stabilizes.

---

## Implications for Roadmap

### Phase 1: API Foundation and Verification
**Rationale:** All subsequent phases depend on WCL M+ API parameters working correctly. The `difficulty: 10` constant and `bracket` parameter for keystone filtering are MEDIUM-confidence inferences — they must be verified against the live API before any tooling is built. One incorrect assumption here cascades into a rewrite.
**Delivers:** `"mythic_plus": 10` in `DIFFICULTY_MAP`, verified query patterns, new Pydantic models (`MplusBenchmarkBundle`, `MplusCoachingResponse`, `SegmentAnalysis`), updated `_query_all_fights` with `keystoneBonus`/`keystoneLevel`
**Addresses:** M+ benchmark aggregation foundation; run quality classification
**Avoids:** Pitfall 1 (API structure), Pitfall 4 (data quality / partial logs)

### Phase 2: Benchmark Orchestrator (`get_mplus_benchmarks`)
**Rationale:** Every comparison feature depends on benchmark data. This is the critical path dependency. The implementation is mostly mechanical — call existing tools with `difficulty="mythic_plus"` — so implementation risk is low once Phase 1 API verification passes. Benchmarks cached at this phase make Phases 3 and 4 cheap to iterate on.
**Delivers:** `_mplus_benchmarks.py` + `get_mplus_benchmarks` MCP tool; `MplusBenchmarkBundle` with rotation/timeline/defensive benchmarks per dungeon; 6h cache with affix-aware invalidation
**Uses:** Existing `get_rotation_profile`, `get_cooldown_timelines`, `get_defensive_patterns` — all already parameterized on difficulty
**Avoids:** Pitfall 2 (rate limits — per-dungeon lazy build, sample_size=5), Pitfall 3 (key level — bracket filtering), Pitfall 5 (staleness — affix in cache key)

### Phase 3: Comparison Engine (`_mplus_comparisons.py`)
**Rationale:** This is the only phase with substantial new logic. Full-run comparison delegates to existing `_analysis_comparisons.py` functions. New logic covers: per-segment DPS comparison, CD waste detection, death correlation with defensive availability. Build and test independently before wiring into the coaching tool.
**Delivers:** `_mplus_comparisons.py` with `compare_mplus_full_run`, `analyze_segment_performance`, `analyze_death_patterns`; CD waste detection; per-segment weakness identification
**Avoids:** Pitfall 6 (DPS metric confusion — active DPS only), Pitfall 7 (non-additive segment DPS averaging), Pitfall 9 (segment classification fragility — validate against known boss encounter IDs)

### Phase 4: Coaching Tool (`coach_dungeon_run`)
**Rationale:** Orchestrates all prior phases. The two-phase flow (identify dungeon -> parallel fetch player data + benchmarks -> compare -> output) is well-specified. Primary work is wiring components together, structuring `MplusCoachingResponse` for clear Claude interpretation, and integration testing with real M+ logs.
**Delivers:** `mplus_coach.py` + `coach_dungeon_run` MCP tool registered in `server.py`; updated `coaching.py` with M+ workflow guidance; integration tests with real logs
**Implements:** Full three-tool architecture; parallel data fetch pattern
**Avoids:** Pitfall 10 (raid architecture reuse — M+-specific orchestration, not parameterized raid tools with `if is_dungeon:` branches)

### Phase Ordering Rationale

- Phase 1 must be first because MEDIUM-confidence API assumptions underpin everything. A failed `difficulty: 10` live query is a full blocker for all other phases.
- Phase 2 before Phase 3/4 because benchmarks are the data dependency for all comparison work; once cached, Phases 3 and 4 cost nothing to test in iteration.
- Phase 3 before Phase 4 because `coach_dungeon_run` consumes `_mplus_comparisons.py` directly.
- The parallel benchmark fetch pattern (`asyncio.gather`) is already established in the codebase — replicate it in `_mplus_benchmarks.py` and in `coach_dungeon_run`'s Phase 1b.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1:** Live WCL API verification required before writing a single line of M+ tooling. Specifically: confirm `difficulty: 10`, confirm `bracket` accepts keystone level as integer, confirm shape of M+ ranking entries (does `report.fightID` point to aggregate fight?), confirm dungeon encounter ID discovery from `get_encounters(content_type="mythic_plus")`.

Phases with standard patterns (skip research-phase):
- **Phase 2:** Purely mechanical after Phase 1 verification — extend existing difficulty abstraction
- **Phase 3:** Logic is well-specified from codebase analysis; no external API uncertainty
- **Phase 4:** Wiring and integration testing; no new patterns beyond what Phases 1-3 establish

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | No new dependencies; existing infrastructure verified by working codebase |
| Features | MEDIUM | Feature set well-researched; WCL-specific fields for some features (keystoneBonus, keystoneAffixes) need implementation verification |
| Architecture | HIGH | Existing codebase patterns fully understood; component boundaries clear; only WCL API shape assumptions are MEDIUM |
| Pitfalls | HIGH | Derived from direct codebase analysis + WCL documentation; specific values (difficulty=10) are MEDIUM confidence |

**Overall confidence:** MEDIUM — architecture and plan are solid; the WCL M+ API parameter verification in Phase 1 is the single gating uncertainty.

### Gaps to Address

- **`difficulty: 10` for M+:** Inferred from WCL v1 docs, community wrappers, and forum discussions. Must be confirmed via live GraphQL introspection or test query before Phase 2. Fallback: query without difficulty parameter to observe what WCL defaults to for dungeon encounters.
- **`bracket` as keystone level integer:** Documented as "brackets are keystone levels for M+" but not live-verified. If `bracket: 12` does not work as a raw integer, investigate WCL's keystone bracket encoding.
- **Dungeon encounter ID shape:** `get_encounters(content_type="mythic_plus")` is expected to return one encounter per dungeon zone. Verify against a live Season 1 TWW zone query — if zones have no encounters listed, investigate the M+ season zone structure.
- **`keystoneBonus` and `keystoneLevel` availability:** Cited in WCL `ReportFight` schema docs but not currently queried by `_query_all_fights`. Verify field names and add to query in Phase 1.
- **M+ rankings sparsity at low key levels:** Low-key brackets (e.g., +5, +6) may have too few WCL rankings for meaningful benchmarks. Establish a minimum sample size threshold; define fallback strategy (adjacent bracket + disclosure in output).

---

## Sources

### Primary (HIGH confidence)
- Existing codebase: `src/tools/dungeon_analysis.py`, `src/tools/builds.py`, `src/tools/timelines.py`, `src/tools/rotation.py`, `src/tools/defensives.py`, `src/tools/_analysis_comparisons.py`
- Existing codebase: `src/wcl_client.py`, `src/cache.py`, `src/models.py`
- Previous research: `.planning/quick/260328-l78-build-analyze-dungeon-run-tool-aggregate/260328-l78-RESEARCH.md`
- Keystone Heroes (archived) — `https://github.com/ljosberinn/keystone-heroes` — M+ analysis feature reference and WCL data model validation

### Secondary (MEDIUM confidence)
- WCL API v2 Encounter docs — `https://www.warcraftlogs.com/v2-api-docs/warcraft/encounter.doc.html`
- WCL Rankings guide — `https://www.warcraftlogs.com/help/ranks/` — bracket = keystone level for M+
- WCL ReportFight schema — `https://www.warcraftlogs.com/v2-api-docs/warcraft/reportfight.doc.html` — keystoneLevel, keystoneBonus, keystoneAffixes
- WCL M+ Rankings discussion — `https://forums.combatlogforums.com/t/mythic-dungeons-rankings-discussion/662`
- WCL Rate Limit Forum — 3600 pts/hour, 1-hour cycle reset — `https://forums.combatlogforums.com/t/api-rate-limit-and-points-spent/10320`
- Peak of Serenity M+ log analysis methodology — `https://www.peakofserenity.com/tww/windwalker/pve-guide/log-analysis/mythicplus/`
- Active DPS explanation — `https://onlyfarms.gg/wiki/world-of-warcraft/active-dps`

### Tertiary (LOW confidence)
- WCL v1 API pattern showing `difficulty=10` for Mythic+/Dungeons/CMs — inferred from community wrapper packages and search results; not directly verified in v2 GraphQL schema
- `bracket` parameter accepting keystone integer directly — inferred from docs description; not verified via live query
- M+ rankings sparsity at low key levels — inferred from general ranking distribution patterns; not empirically measured

---
*Research completed: 2026-03-28*
*Ready for roadmap: yes*
