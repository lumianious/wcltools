# Requirements: WoW Coach MCP Server

**Defined:** 2026-03-28
**Core Value:** Claude can tell a player exactly what to improve — backed by data from what top players actually do.

## v2.0 Requirements

Requirements for M+ Coaching Intelligence milestone. Each maps to roadmap phases.

### Benchmarks

- [x] **BENCH-01**: Agent can query WCL M+ leaderboard for top DPS players in a specific dungeon+spec+key level (e.g., top Balance Druids in +10 Magisters' Terrace)
- [x] **BENCH-02**: From top player reports, agent can extract per-trash-segment (boss-bounded) spell damage % and major CD timing
- [x] **BENCH-03**: From top player reports, agent can extract cast-level data for boss encounters within the dungeon
- [x] **BENCH-04**: Benchmark data is cached per dungeon+spec+key level combination

### Cooldown Analysis

- [x] **CD-01**: Agent can retrieve major CD usage (offensive 1min/2min/3min, defensive, pots) from top players across boss-bounded trash segments
- [x] **CD-02**: Agent can show CD spacing pattern across the full dungeon — which trash segment gets which CDs

### Damage Profile

- [x] **DMG-01**: Agent can retrieve per-trash-segment spell damage % distribution from top players
- [x] **DMG-02**: Agent can compare player's spell damage % per trash segment against benchmark

### Boss Analysis

- [ ] **BOSS-01**: Agent can run raid-style cast-by-cast analysis on each boss within a M+ dungeon (reuse existing analyze_player_log patterns)
- [ ] **BOSS-02**: Agent can compare player's boss performance against top-player benchmarks (rotation, CDs, defensives)

### Survival

- [x] **SURV-01**: Agent can retrieve defensive CD usage patterns from top M+ players across boss-bounded segments
- [ ] **SURV-02**: Agent can analyze player deaths with damage-taken breakdown and defensive availability check

### Interrupts

- [x] **INT-01**: Agent can retrieve interrupt cast counts and targets from top M+ players per dungeon
- [x] **INT-02**: Agent can compare player's interrupt usage against benchmark (count, critical kicks missed)

### Coaching

- [ ] **COACH-01**: Agent can produce per-segment coaching — aggregate style for trash segments, cast-by-cast for bosses
- [ ] **COACH-02**: Agent can produce whole-dungeon summary with benchmark comparison (overall DPS gap, total CD efficiency, deaths, biggest improvement areas)
- [ ] **COACH-03**: Coaching output includes both structured gap data and natural language actionable advice

## Design Decisions

- **Trash segments defined by boss boundaries:** [before boss1] -> Boss1 -> [boss1->boss2] -> Boss2 -> etc. This sidesteps route-matching problems.
- **Trash = aggregate analysis:** Spell damage %, major CD placement, defensive usage. Not cast-by-cast.
- **Boss = cast-by-cast analysis:** Reuse raid-style APL/rotation analysis from existing tools.
- **Benchmark source:** WCL M+ leaderboard filtered by dungeon+spec+key level (e.g., top +10 Balance Druids in Magisters' Terrace).
- **Major CDs only:** Track offensive CDs (1min/2min/3min), defensive CDs, pots. Not every spell.

## v2.1 Requirements

Deferred to future release.

### Enhanced Coaching

- **META-01**: Cross-dungeon meta analysis — which dungeons are player's strongest/weakest
- **SCALE-01**: Key level scaling context — expected DPS at each key level tier

## Out of Scope

| Feature | Reason |
|---------|--------|
| Route optimization / MDT integration | Different data pipeline, not WCL-based |
| Cross-dungeon meta analysis | Multi-report orchestration — v2.1 |
| Group composition analysis | 5x API budget, different coaching model |
| Healer/tank coaching | DPS focus first |
| Per-trash-pack matching | Route-dependent, unreliable — use boss-bounded segments |
| Real-time / live coaching | MCP is post-game analysis only |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BENCH-01 | Phase 8 | Complete |
| BENCH-02 | Phase 9 | Complete |
| BENCH-03 | Phase 9 | Complete |
| BENCH-04 | Phase 8 | Complete |
| CD-01 | Phase 9 | Complete |
| CD-02 | Phase 9 | Complete |
| DMG-01 | Phase 9 | Complete |
| DMG-02 | Phase 10 | Complete |
| BOSS-01 | Phase 10 | Pending |
| BOSS-02 | Phase 10 | Pending |
| SURV-01 | Phase 9 | Complete |
| SURV-02 | Phase 10 | Pending |
| INT-01 | Phase 9 | Complete |
| INT-02 | Phase 10 | Complete |
| COACH-01 | Phase 11 | Pending |
| COACH-02 | Phase 11 | Pending |
| COACH-03 | Phase 11 | Pending |

**Coverage:**
- v2.0 requirements: 17 total
- Mapped to phases: 17/17
- Unmapped: 0

---
*Requirements defined: 2026-03-28*
*Last updated: 2026-03-28 after roadmap creation*
