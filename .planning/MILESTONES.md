# Milestones

## v2.0 M+ Coaching Intelligence (Shipped: 2026-03-29)

**Phases completed:** 4 phases, 10 plans, 15 tasks

**Key accomplishments:**

- Status:
- M+ rankings query infrastructure with difficulty=10 queries, bracket filtering, sparse fallback to adjacent key levels, and 6-hour cache per dungeon+spec+key combination.
- 4 Pydantic models for M+ benchmark segments, 5 fixture constants, and 11-test scaffold (4 GREEN, 7 RED) covering all phase requirements
- 5 pure extraction functions + async WCL query helpers + per-report orchestrator for boss-bounded M+ benchmark data
- Cross-player median aggregation pipeline with asyncio.Semaphore(3) parallel fetching, 6h cache, and registered MCP tool get_mplus_benchmarks
- Pydantic models for M+ comparison results plus trash damage gap analysis (DMG-02) and interrupt comparison with critical missed target detection (INT-02)
- Boss cast-by-cast comparison with CD gap detection plus per-death damage breakdown with three-state defensive availability classification
- compare_mplus_run MCP tool wiring full pipeline: player data extraction, benchmark alignment, per-segment gap analysis, boss cast-by-cast comparison, death breakdown with defensive check, and flag summary
- Pure-function coaching logic converting MplusComparisonResponse into prioritized CoachingItems with dual structured+NL format, top 3 per segment, top 5 overall
- coach_mplus_run registered as MCP tool #18 with full docstring, parameter forwarding, and model_dump serialization

---
