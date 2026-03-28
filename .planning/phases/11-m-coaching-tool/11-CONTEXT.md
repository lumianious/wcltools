# Phase 11: M+ Coaching Tool - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the coaching output layer that takes Phase 10's comparison results and produces actionable per-segment coaching for an entire M+ dungeon run. Delivers both structured gap data (machine-readable for programmatic use) and natural language actionable advice (human-readable for Claude to present). One new MCP tool: `coach_mplus_run`.

</domain>

<decisions>
## Implementation Decisions

### Coaching Output Structure
- Per-trash-segment coaching: aggregate style with top 3 gaps + actionable fix per gap — per COACH-01 "aggregate style for trash segments"
- Per-boss coaching: cast-by-cast style with top 3 priority rotation/CD issues per boss — per COACH-01
- Whole-dungeon summary: dashboard-style with overall DPS gap, total CD efficiency, death count, top 3 improvement areas — per COACH-02
- Dual output: response includes both `structured` field (machine-readable gaps) and `coaching_text` field (natural language advice) — per COACH-03

### Coaching Intelligence
- Priority ranking: sort improvement areas by estimated DPS impact (damage gain from fixing gap) — most actionable for the player
- Suggestion count: top 3 per segment, top 5 overall — focused enough to be actionable without overwhelming
- Natural language tone: direct, specific, actionable — "In trash before Boss 2, use Incarnation on pull. Benchmark players average 45% damage from Starfall here vs your 28%"
- Include brief positive feedback: note segments where player meets/exceeds benchmark — builds motivation, shows analysis is fair

### Tool Interface
- New MCP tool `coach_mplus_run` — separation of concerns; compare returns data, coach returns advice
- Parameters: same as compare — `report_code, player_name, encounter_id, spec, key_level, fight="last"`
- Calls `compare_mplus_run` internally — single MCP call for the user, comparison is an implementation detail

### Claude's Discretion
- Internal module structure
- Pydantic model field names (follow existing conventions)
- Exact DPS impact estimation formula
- How to phrase positive feedback vs gaps
- Whether to deduplicate similar advice across segments

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mplus_comparison.py:compare_mplus_run` — returns MplusComparisonResponse with segment_comparisons, boss_comparisons, death_analysis, summary
- `models.py` — Phase 10 comparison models (SegmentComparison, BossComparison, DeathBreakdown, ComparisonSummary)
- `coaching.py:get_coaching_context` — existing raid coaching tool, shows how to structure coaching output
- All Phase 9+10 infrastructure (benchmarks + comparison) is called internally

### Established Patterns
- Coaching tools return structured responses that Claude interprets for the user
- Chinese spell names via `get_spell_name` and `get_talent_name`
- MCP tool registration pattern in `server.py`

### Integration Points
- `server.py` — register `coach_mplus_run` as MCP tool #18
- `models.py` — add coaching response models
- `mplus_comparison.py` — import and call `compare_mplus_run`

</code_context>

<specifics>
## Specific Ideas

- Phase 10's ComparisonSummary already has `overall_dps_gap_pct`, `segments_below_threshold`, `total_deaths`, `critical_interrupts_missed` — directly maps to COACH-02 dashboard
- Each SegmentComparison has `damage_gaps[]` with gap_pct and benchmark values — sort by gap_pct × segment duration for DPS impact estimate
- Boss comparisons include cast gaps and CD gaps — transform into "you missed N casts of X" advice
- Death breakdowns include defensive availability — transform into "you died at 2:15 but Barkskin was available" advice

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 11-m-coaching-tool*
*Context gathered: 2026-03-29*
