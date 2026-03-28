# WoW Coach MCP Server

## What This Is

An MCP server that feeds WarcraftLogs data into Claude, enabling Claude to act as a personal raid and M+ coach. Interprets aggregate top-player behavior and combines it with deep class/rotation knowledge to give boss-specific, dungeon-specific, actionable advice. User plays WoW with the Chinese client.

## Core Value

Claude can tell a player exactly what to improve in their gameplay — backed by data from what top players actually do, not generic guides.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. Phases 1-7 complete. -->

- ✓ **RAID-01**: Discover current bosses/dungeons — `get_encounters` (Phase 1)
- ✓ **RAID-02**: Aggregate talent/gear/stat meta from top parsers — `get_top_builds` (Phase 2)
- ✓ **RAID-03**: Static class/spec/spell data — `get_spec_info` (Phase 2)
- ✓ **RAID-04**: Cooldown cast timeline aggregation from top players — `get_cooldown_timelines` (Phase 3)
- ✓ **RAID-05**: Cast counts, CPM, buff uptimes from top players — `get_rotation_profile` (Phase 4)
- ✓ **RAID-06**: Defensive ability timing clusters — `get_defensive_patterns` (Phase 4)
- ✓ **RAID-07**: Personal log analysis with gap analysis — `analyze_player_log` (Phase 5)
- ✓ **RAID-08**: Top parse replay URLs — `get_example_logs` (Phase 5)
- ✓ **RAID-09**: Advanced APL analysis, eclipse metrics, CD windows — (Phase 6)
- ✓ **RAID-10**: Boss cast timeline, cast sequence, buff/resource timelines — (Phase 7)
- ✓ **RAID-11**: Coaching context for session setup — `get_coaching_context` (Phase 7)
- ✓ **MPLUS-01**: M+ dungeon run analysis (per-run DPS, damage, deaths, segments) — `analyze_dungeon_run` (Quick task)

### Active

<!-- Current scope: M+ Coaching Intelligence (v2.0) -->

- [ ] M+ benchmark aggregation from WCL rankings
- [ ] M+ cooldown timeline across all dungeon segments
- [ ] M+ rotation profile (per-dungeon benchmarks)
- [ ] M+ defensive patterns
- [ ] M+ death analysis
- [ ] M+ per-segment coaching with gap analysis

### Out of Scope

- Real-time overlay / in-game addon — MCP is analysis-only, not live
- Healer/tank-specific coaching — DPS focus first
- PvP analysis — different data model entirely
- Pasteable talent import strings — WCL doesn't expose import format

## Context

- **WCL API**: GraphQL v2, OAuth client_credentials, 3600 points/hour rate limit
- **Season**: Midnight Season 1 (launched March 17, 2026), expansion ID 7
- **Data**: Lorrgs-exported spell/boss data (English), Blizzard API talent names (Chinese)
- **MCP SDK**: Pinned to mcp>=1.25,<2 (v2 pre-alpha)
- **Transport**: stdio (stdout = JSON-RPC, logging → stderr)
- **Existing tools**: 15 registered MCP tools covering raid coaching end-to-end
- **M+ data structure**: WCL reports group fights by `gameZone`; each dungeon run has segment fights + optional aggregate fight

## Constraints

- **Rate limits**: 3600 WCL points/hour — M+ analysis must budget carefully (multiple reports for benchmarks)
- **Language**: Chinese for user-facing spell/talent names (zh_CN locale)
- **MCP SDK**: Pin to v1.x, no v2 migration
- **API only**: WCL M+ rankings endpoint structure may differ from raid rankings — verify during research

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Lorrgs as reference-only (not imported) | AWS deps too heavy to stub | ✓ Good |
| File-based JSON cache with TTL | Simple, no external deps | ✓ Good |
| Chinese talent names via Blizzard API bridge | WCL uses internal IDs, need 3-system bridge | ✓ Good |
| gameZone-based run detection for M+ | Reliable grouping even with multi-dungeon reports | ✓ Good |
| Active DPS (sum of fight durations) not wall-clock | Avoids misleading low DPS from between-pull downtime | ✓ Good |

## Current Milestone: v2.0 M+ Coaching Intelligence

**Goal:** Enable Claude to coach M+ dungeon performance by comparing player data against top-player benchmarks across all dungeon segments.

**Target features:**
- M+ benchmark aggregation from WCL rankings
- M+ cooldown timeline (full-run CD spacing across all segments)
- M+ rotation profile (per-dungeon benchmarks from top players)
- M+ defensive patterns for dungeon segments
- M+ death analysis correlated with incoming damage
- M+ per-segment coaching with structured gap analysis + actionable advice

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-28 after milestone v2.0 initialization*
