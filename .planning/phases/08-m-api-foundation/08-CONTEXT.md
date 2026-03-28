# Phase 8: M+ API Foundation - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Verify WCL M+ API parameters work correctly, add `mythic_plus` difficulty support to the existing infrastructure, create new Pydantic models for M+ benchmark data, and establish cache key strategy for M+ benchmarks. This is a foundation phase — no user-facing MCP tools are added, but all downstream phases depend on these API patterns being verified and working.

</domain>

<decisions>
## Implementation Decisions

### API Verification Approach
- **D-01:** Claude's Discretion — use a script-first approach: write a verification script that queries WCL live with M+ parameters (difficulty=10, bracket filtering, dungeon encounter IDs), prints the response structure, and documents results. Build code only after verification passes.

### Key Level Handling
- **D-02:** Accept raw integer for key level (e.g., `bracket=10` for a +10 key). If a bracket has sparse data (fewer than 3 players), fall back to adjacent bracket (e.g., +10 → try +11, then +9) and disclose the bracket gap in output.
- **D-03:** Do NOT normalize or aggregate across key levels — always compare within the same bracket.

### Sample Size for M+
- **D-04:** Use 5 top players for M+ benchmarks (not 50 like raid). Rationale: M+ reports are larger (30+ min, many segments), so each report costs more API points. 5 players gives sufficient signal while keeping rate limit budget manageable (~20-50 points per dungeon benchmark).

### Claude's Discretion
- API verification script structure and error handling
- Exact Pydantic model field names (follow existing naming conventions)
- Cache TTL for M+ benchmarks (research suggests 6h, align with raid benchmark TTL)
- Whether to add `keystoneLevel`/`keystoneBonus` fields to `_query_all_fights` in this phase or Phase 9

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### WCL API
- `src/tools/builds.py` — `DIFFICULTY_MAP` (line 49), `SPEC_MAPPING`, `characterRankings` query pattern
- `src/tools/timelines.py` — Benchmark aggregation pipeline (rankings → reports → events → aggregate → cache)
- `src/tools/examples.py` — Rankings query with `DIFFICULTY_MAP` usage pattern

### Existing M+ Infrastructure
- `src/tools/dungeon_analysis.py` — `gameZone`-based run grouping, `_query_all_fights`, `DungeonRun` class
- `src/models.py` — Existing Pydantic models, `DungeonRunAnalysisResponse`, `FightSegmentSummary`

### Research
- `.planning/research/STACK.md` — WCL M+ API query details, `difficulty: 10`, `bracket` parameter
- `.planning/research/SUMMARY.md` — Architecture approach, rate limit budget, pitfall mitigations
- `.planning/research/PITFALLS.md` — 13 pitfalls with phase-specific warnings

### Project Context
- `../wow-mcp-handoff-final.md` — WCL API reference, rate limit system, known gotchas

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `DIFFICULTY_MAP` in `builds.py:49` — single point to add `"mythic_plus": 10`
- `SPEC_MAPPING` in `builds.py` — 39-spec slug-to-WCL-class mapping, reusable as-is
- `characterRankings` GraphQL pattern — used in 5+ tools, all parameterized on difficulty
- `FileCache` in `cache.py` — file-based JSON cache with TTL, key-string-based (difficulty already in keys)
- `DungeonRun` class in `dungeon_analysis.py` — gameZone-based grouping for run detection

### Established Patterns
- All benchmark tools: `DIFFICULTY_MAP.get(difficulty, 4)` → integer for GraphQL
- Rankings query: `worldData.encounter(id: N).characterRankings(className, specName, metric, difficulty, ...)`
- Cache keys include difficulty string — M+ benchmarks will naturally namespace with `"mythic_plus"` in key

### Integration Points
- `builds.py:DIFFICULTY_MAP` — add `"mythic_plus": 10` entry
- `models.py` — add new M+ benchmark models
- `dungeon_analysis.py:_query_all_fights` — optionally add `keystoneLevel`/`keystoneBonus` fields
- `encounters.py:get_encounters` — verify M+ dungeons appear with `content_type="mythic_plus"`

</code_context>

<specifics>
## Specific Ideas

- Research indicates `difficulty: 10` for M+ and `bracket` parameter filters by keystone level. MEDIUM confidence — needs live verification before building.
- WCL M+ rankings may return `report.fightID` pointing to the aggregate dungeon fight (encounterID > 0), not individual segments. Verify this.
- Encounter ID for M+ dungeons: verify whether `get_encounters(content_type="mythic_plus")` returns dungeon-level encounter IDs matching the zone encounters seen in real reports (e.g., eid=112526 for Algeth'ar Academy, eid=12811 for Magisters' Terrace).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 08-m-api-foundation*
*Context gathered: 2026-03-28*
