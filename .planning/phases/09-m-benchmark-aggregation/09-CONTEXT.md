# Phase 9: M+ Benchmark Aggregation - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a benchmark aggregation system that collects comprehensive M+ performance data from top players for any dungeon. Given a dungeon+spec+key level, the system fetches top player reports (from Phase 8 rankings), extracts per-segment metrics (damage, CDs, defensives, interrupts), and caches the aggregated benchmark bundle. This phase delivers one new MCP tool (`get_mplus_benchmarks`) and the internal pipeline that powers it.

</domain>

<decisions>
## Implementation Decisions

### Data Collection Pipeline
- Reuse the timelines.py pipeline pattern (rankings → reports → events → aggregate → cache) adapted for M+ segment structure
- Query all 5 top-player reports in parallel with asyncio.Semaphore(3) to balance latency vs rate limit safety
- Cache the final aggregate only (not per-report raw data) — cache key `mplus_bench:{spec}:{encounter_id}:k{level}:segments`, one object per dungeon benchmark
- Align segments by boss-bounded positions (trash-before-B1, B1, trash-B1→B2, B2, etc.) to sidestep route differences across top players — per REQUIREMENTS design decision

### Benchmark Data Model
- Per trash segment: spell damage % + top-N spell breakdown + major CD casts + defensive CDs + interrupt count — covers BENCH-02, CD-01, DMG-01, SURV-01, INT-01 in one collection pass
- "Major CD" = CD >= 30s, tagged dps/defensive/raid_cd — consistent with timelines.py criteria and existing spell data
- Boss encounters: cast-level benchmarks (reuse cast-sequence/rotation patterns from raid tools) — per BENCH-03
- Aggregation across 5 players: use median of per-segment metrics, aligned by boss-bounded segment position. Median is robust to outliers

### Tool Interface & Storage
- Single MCP tool `get_mplus_benchmarks` returns full dungeon benchmark bundle (all segments, all data types)
- Parameters: `spec, encounter_id, key_level` — matches Phase 8's `query_mplus_rankings` signature
- Response: hierarchical — segments[] with {damage_breakdown, cd_casts, defensive_cds, interrupts}, plus separate boss_benchmarks[] with cast-level data
- Lazy fetch on first request, cache 6h (matches raid TTL and Phase 8 rankings cache). Budget: ~25-35 WCL points per dungeon benchmark (5 reports × 5-7 queries)

### Claude's Discretion
- Internal module structure (single file vs split by concern)
- Exact Pydantic model field names (follow existing naming conventions)
- Error handling for reports with missing segment data
- Top-N spell count for damage breakdown

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mplus_rankings.py:query_mplus_rankings` — returns (meta, entries) with report_code and fight_id for each top player
- `timelines.py` — complete pipeline pattern: rankings → reports → masterData → events → aggregate → cache
- `dungeon_analysis.py:_query_all_fights` — fetches all fights with gameZone, keystoneLevel, keystoneBonus
- `dungeon_analysis.py:_group_fights_by_dungeon` / `_classify_segments` — boss/trash segment classification
- `rotation.py:_query_cast_events` / `_query_buff_table` / `_query_master_data` — report-level queries
- `analyze.py:_query_damage_done` / `_query_death_events` — damage and death event queries
- `cache.py:cache_get/cache_set` — file-based JSON cache with TTL
- `builds.py:SPEC_MAPPING` — 39-spec slug mapping
- `data/:get_spec_spells` — spell metadata with tags and cooldowns for major CD identification

### Established Patterns
- Rankings → report.code + report.fightID → per-report queries (masterData, events, tables)
- All benchmark tools cache at 6h TTL
- Event pagination via `nextPageTimestamp` for large data sets
- Chinese spell names via `get_spell_name` and `get_talent_name`

### Integration Points
- `server.py` — register new `get_mplus_benchmarks` MCP tool
- `models.py` — add new Pydantic models for M+ benchmark segments
- `mplus_rankings.py` — call `query_mplus_rankings` to get top player report codes

</code_context>

<specifics>
## Specific Ideas

- Phase 8 verified: `difficulty: 10` for M+, `bracket` is minimum-level filter with sparse fallback
- Each MplusRankingEntry has `report_code` and `fight_id` — use these to fetch per-report segment data
- `_query_all_fights` already returns keystoneLevel/keystoneBonus/keystoneAffixes/keystoneTime fields
- Boss-bounded segment alignment: use encounterID > 0 fights as boundaries, trash segments are gaps between bosses
- Interrupt data requires `dataType: Casts` with interrupt-tagged spell IDs from spec spell data

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 09-m-benchmark-aggregation*
*Context gathered: 2026-03-28*
