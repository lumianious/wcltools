# Roadmap: WoW Coach MCP Server

## Milestones

- ✅ **v1.0 Raid Coaching** - Phases 1-7 (shipped)
- 🚧 **v2.0 M+ Coaching Intelligence** - Phases 8-11 (in progress)

## Phases

<details>
<summary>v1.0 Raid Coaching (Phases 1-7) - SHIPPED</summary>

- [x] **Phase 1: Encounter Discovery** - Discover current bosses/dungeons
- [x] **Phase 2: Build Meta** - Aggregate talent/gear/stat meta from top parsers
- [x] **Phase 3: Cooldown Timelines** - Cooldown cast timeline aggregation
- [x] **Phase 4: Rotation & Defensives** - Cast counts, CPM, buff uptimes, defensive patterns
- [x] **Phase 5: Personal Analysis** - Player log analysis with gap analysis
- [x] **Phase 6: Advanced Analysis** - APL analysis, eclipse metrics, CD windows
- [x] **Phase 7: Boss Timeline & Coaching** - Boss cast timeline, coaching context

</details>

### v2.0 M+ Coaching Intelligence

- [x] **Phase 8: M+ API Foundation** - Verify WCL M+ API parameters, add difficulty support, models, caching
- [ ] **Phase 9: M+ Benchmark Aggregation** - Build cached benchmark bundle from top M+ players per dungeon
- [ ] **Phase 10: M+ Comparison Engine** - Per-segment comparison, boss analysis, death analysis, interrupt comparison
- [ ] **Phase 11: M+ Coaching Tool** - Orchestrate all data into structured coaching output with actionable advice

## Phase Details

### Phase 8: M+ API Foundation
**Goal**: Agent can query WCL for M+ ranking and report data with verified API parameters
**Depends on**: Phase 7 (existing raid infrastructure)
**Requirements**: BENCH-01, BENCH-04
**Success Criteria** (what must be TRUE):
  1. Agent can query WCL M+ leaderboard for top players in a specific dungeon+spec+key level and receive valid ranking results
  2. Agent can filter M+ rankings by keystone level bracket (e.g., +10, +12) without cross-bracket contamination
  3. M+ benchmark data is cached per dungeon+spec+key level combination with appropriate TTL
  4. Dungeon run queries include keystoneLevel and keystoneBonus fields for run quality classification
**Plans**: 2 plans

Plans:
- [x] 08-01-PLAN.md — Verify WCL M+ API parameters live (difficulty=10, bracket, encounter IDs, keystone fields)
- [x] 08-02-PLAN.md — Add mythic_plus difficulty, Pydantic models, keystone fields, M+ rankings query with cache

### Phase 9: M+ Benchmark Aggregation
**Goal**: Agent can retrieve comprehensive benchmark data from top M+ players for any dungeon segment
**Depends on**: Phase 8
**Requirements**: BENCH-02, BENCH-03, CD-01, CD-02, DMG-01, SURV-01, INT-01
**Success Criteria** (what must be TRUE):
  1. Agent can retrieve per-trash-segment (boss-bounded) spell damage % and major CD timing from top players
  2. Agent can retrieve cast-level benchmark data for boss encounters within M+ dungeons
  3. Agent can show CD spacing pattern across the full dungeon run — which trash segment gets which CDs
  4. Agent can retrieve defensive CD usage patterns and interrupt counts from top M+ players per dungeon segment
  5. Benchmark queries use lazy per-dungeon fetching and respect rate limits (sample_size ~5 reports)
**Plans**: 3 plans

Plans:
- [x] 09-01-PLAN.md — Pydantic models, test fixtures, and failing test scaffold for all 7 requirements
- [ ] 09-02-PLAN.md — Core extraction functions: segment alignment, damage/CD/defensive/interrupt extraction
- [ ] 09-03-PLAN.md — Cross-player aggregation, caching, MCP tool registration, documentation

### Phase 10: M+ Comparison Engine
**Goal**: Agent can compare a player's M+ performance against benchmarks across every dungeon segment
**Depends on**: Phase 9
**Requirements**: DMG-02, BOSS-01, BOSS-02, SURV-02, INT-02
**Success Criteria** (what must be TRUE):
  1. Agent can compare player's spell damage % per trash segment against benchmark and identify gaps
  2. Agent can run raid-style cast-by-cast analysis on each boss within a M+ dungeon and compare against top-player benchmarks
  3. Agent can analyze player deaths with damage-taken breakdown and defensive availability check
  4. Agent can compare player's interrupt usage against benchmark (count and critical kicks missed)
**Plans**: TBD

Plans:
- [ ] 10-01: TBD
- [ ] 10-02: TBD

### Phase 11: M+ Coaching Tool
**Goal**: Agent can produce actionable per-segment coaching for an entire M+ dungeon run
**Depends on**: Phase 10
**Requirements**: COACH-01, COACH-02, COACH-03
**Success Criteria** (what must be TRUE):
  1. Agent can produce per-segment coaching — aggregate style for trash segments, cast-by-cast for bosses
  2. Agent can produce whole-dungeon summary with benchmark comparison (overall DPS gap, total CD efficiency, deaths, biggest improvement areas)
  3. Coaching output includes both structured gap data (machine-readable) and natural language actionable advice (human-readable)
**Plans**: TBD

Plans:
- [ ] 11-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 8 -> 9 -> 10 -> 11

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1-7 | v1.0 | 7/7 | Complete | shipped |
| 8. M+ API Foundation | v2.0 | 2/2 | Complete | 2026-03-28 |
| 9. M+ Benchmark Aggregation | v2.0 | 0/3 | Not started | - |
| 10. M+ Comparison Engine | v2.0 | 0/? | Not started | - |
| 11. M+ Coaching Tool | v2.0 | 0/? | Not started | - |
