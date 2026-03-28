# Phase 10: M+ Comparison Engine - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Build a comparison engine that takes a player's M+ dungeon run and compares it against benchmarks from top players (Phase 9). For each segment: compare damage distribution, CD usage, defensive timing, and interrupt coverage. For each boss: run raid-style cast-by-cast analysis. Produce per-death breakdowns. Delivers one new MCP tool (`compare_mplus_run`) that returns a structured comparison with gaps flagged.

</domain>

<decisions>
## Implementation Decisions

### Comparison Pipeline
- Reuse `analyze_dungeon_run` output for player's per-segment data — it already has damage/deaths. Extend with CD/interrupt extraction using Phase 9 extraction functions (`_extract_segment_cds`, `_count_segment_interrupts`)
- Single `compare_mplus_run` function returns all comparisons in one call — mirrors `get_mplus_benchmarks` pattern
- Gap identification threshold: flag if player is >20% below benchmark median — simple, actionable, avoids noise from minor variance
- Incomplete segments (player died early, fewer bosses): mark as "incomplete" with available data, don't skip — show what data exists + note the gap

### Boss & Death Analysis
- Reuse existing `_analysis_comparisons.py` patterns (compare_rotation, compare_cooldowns, compare_defensives) adapted for M+ boss fights — proven cast-level analysis code from raid coaching
- Per-death breakdown: damage-taken sources + "was defensive available?" check — per SURV-02
- Per-segment interrupt count + critical missed kick identification — per INT-02
- Critical missed interrupts: compare player's interrupt targets vs benchmark interrupt targets — if benchmark interrupts spell X but player doesn't, flag as critical miss

### Tool Interface
- Parameters: `report_code, player_name, encounter_id, spec, key_level, fight="last"` — combines player log identification with benchmark lookup params
- Response: hierarchical matching benchmark structure — segment_comparisons[] + boss_comparisons[] + death_analysis + summary
- Include benchmark values inline in each comparison (player_value, benchmark_value, gap_pct) — Claude needs both numbers to coach

### Claude's Discretion
- Internal module structure (single file vs split)
- Pydantic model field names (follow existing conventions)
- Exact logic for "was defensive available?" check
- How to handle boss fights where player wipes (incomplete boss data)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mplus_benchmarks.py:get_mplus_benchmarks` — returns MplusBenchmarkResponse with segments[] and boss_benchmarks[]
- `dungeon_analysis.py:analyze_dungeon_run` — returns DungeonRunAnalysisResponse with per-segment damage, deaths
- `_analysis_comparisons.py` — compare_rotation, compare_cooldowns, compare_defensives, compare_build, compare_cd_throughput
- `_analysis_metrics.py` — analyze_deaths, analyze_downtime, analyze_cd_windows
- `mplus_benchmarks.py:_extract_segment_cds`, `_count_segment_interrupts` — per-segment extraction (reuse for player data)
- `mplus_benchmarks.py:_build_segment_positions` — boss-bounded segment alignment

### Established Patterns
- All comparison tools return structured dicts with player_value, benchmark_value, gap info
- Death analysis correlates damage-taken events with defensive availability
- Boss analysis uses cast-sequence comparison against benchmark timelines

### Integration Points
- `server.py` — register new `compare_mplus_run` MCP tool
- `models.py` — add Pydantic models for comparison results
- `mplus_benchmarks.py` — call `get_mplus_benchmarks` for benchmark data
- `dungeon_analysis.py` — call `analyze_dungeon_run` or reuse its internal functions for player data

</code_context>

<specifics>
## Specific Ideas

- Phase 9 benchmark response includes `segments[].damage_breakdown`, `segments[].cd_casts`, `segments[].defensive_cds`, `segments[].interrupt_count`, and `boss_benchmarks[]` with cast-level data
- For trash segment comparison: align player's segments to benchmark by boss-bounded position (same scheme as Phase 9)
- For boss comparison: can reuse `_analysis_comparisons.compare_rotation` if player's cast events and benchmark's cast events are in the same format
- Death analysis needs WCL `dataType: Deaths` events — already available via `analyze.py:_query_death_events`

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 10-m-comparison-engine*
*Context gathered: 2026-03-29*
