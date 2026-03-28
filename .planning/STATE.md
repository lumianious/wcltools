# Project State

**Project:** WoW Coach MCP Server
**Current milestone:** v2.0 — M+ Coaching Intelligence
**Last activity:** 2026-03-28 — Milestone v2.0 started

## Current Position

Phase: Not started (defining requirements)
Plan: —
Status: Defining requirements

## Accumulated Context

- Phases 1-7 complete (raid coaching tools, 15 MCP tools registered)
- Quick task: `analyze_dungeon_run` built and bug-fixed (gameZone-based run selection)
- WCL M+ reports use `gameZone` to group fights by dungeon
- M+ rankings API structure needs verification (may differ from raid rankings)
- CD spacing on trash between bosses is highest-priority coaching question

## Session Continuity

### Blockers/Concerns

None.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260328-l78 | Build analyze_dungeon_run tool — aggregate cast/damage data across all fight segments in a M+ run | 2026-03-28 | acae455 | [260328-l78-build-analyze-dungeon-run-tool-aggregate](./quick/260328-l78-build-analyze-dungeon-run-tool-aggregate/) |
