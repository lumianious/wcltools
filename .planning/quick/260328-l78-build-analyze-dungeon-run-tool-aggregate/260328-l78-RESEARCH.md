# Quick Task: Build analyze_dungeon_run Tool - Research

**Researched:** 2026-03-28
**Domain:** WCL API M+ dungeon-wide queries + existing codebase extension
**Confidence:** HIGH (codebase patterns well-understood, WCL API patterns from handoff doc verified)

## Summary

The task is to build `analyze_dungeon_run` -- a new MCP tool that aggregates cast/damage data across all fight segments in an M+ dungeon run. The existing `analyze_player_log` handles single fights only. The new tool queries WCL's `report.table()` and `report.events()` without fight-specific filtering to get dungeon-wide aggregates.

The codebase is well-structured with clear patterns for adding new tools. The main work is: (1) querying all fights in a report to enumerate segments, (2) querying aggregate table data across the full run, (3) optionally iterating events across segments for APL/cast analysis, and (4) assembling a dungeon-level response model.

**Primary recommendation:** Reuse existing WCL query helpers (`_query_master_data`, `_query_cast_events`, `_query_buff_table`, `_query_damage_done`) with modified time ranges covering the full dungeon. Use `report.fights()` without fightIDs to enumerate all segments. Create a new `DungeonRunAnalysisResponse` model and register as a new MCP tool.

## WCL API Patterns for Dungeon-Wide Queries

### Enumerating All Fight Segments

Query `report.fights()` **without** `fightIDs` filter to get all fights in the report:

```graphql
reportData {
  report(code: "CODE") {
    fights {
      id
      startTime
      endTime
      kill
      encounterID
      name
      difficulty
      keystoneLevel
      dungeonPulls { id startTime endTime name }
    }
  }
}
```

**Confidence: MEDIUM** -- `keystoneLevel` and `dungeonPulls` are documented in the WCL schema (referenced in multiple community projects) but not directly verified via live query. The basic `fights` fields (id, startTime, endTime, encounterID, name, kill) are HIGH confidence -- already used in the codebase.

**Fight types in M+ reports:**
- Boss fights have `encounterID > 0` and a boss name
- Trash pulls have `encounterID == 0` and generic names
- The overall dungeon "fight" (fight ID 0 or a special aggregate) may exist depending on the log

### Aggregate Table Queries (No Fight Filtering)

Per handoff doc: `report.table(dataType: DamageDone)` **without** `fightIDs` returns aggregate data across the whole dungeon. Pattern:

```graphql
reportData {
  report(code: "CODE") {
    table(
      startTime: 0
      endTime: 99999999
      sourceID: PLAYER_ID
      dataType: DamageDone
    )
  }
}
```

Omitting `fightIDs` or passing `startTime: 0, endTime: <large number>` covers the entire report. The `table()` query does NOT paginate -- it returns a summary.

**Supported dataTypes for aggregate queries:**
- `DamageDone` -- total damage breakdown by ability (HIGH confidence, already used)
- `Buffs` -- buff uptime percentages across the run (HIGH confidence, already used)
- `Casts` -- cast summary (HIGH confidence)
- `DamageTaken` -- damage taken breakdown (MEDIUM confidence)
- `Deaths` -- death summary (HIGH confidence, already used via events)

### Events Queries Across All Segments

Events queries require `startTime` and `endTime`. For dungeon-wide events:
- Use the first fight's `startTime` and last fight's `endTime`
- Events paginate at ~300/page via `nextPageTimestamp`
- A full M+ run may have 50-100+ pages of cast events

**Important:** Do NOT iterate events for the basic tool. Use `table()` for aggregate metrics. Events pagination across a whole dungeon is expensive (see Rate Limit section).

## Existing Codebase Reuse Map

### Functions to Reuse Directly

| Function | Location | Use For |
|----------|----------|---------|
| `extract_report_code` | `_wcl_helpers.py` | Parse report URL/code |
| `find_actor_id_ci` | `_wcl_helpers.py` | Find player sourceID |
| `_query_master_data` | `rotation.py` | Get actors + ability map |
| `_query_damage_done` | `analyze.py` | Adapt for dungeon-wide query |
| `_query_buff_table` | `rotation.py` | Adapt for dungeon-wide query |
| `_query_cast_events` | `rotation.py` | Adapt for dungeon-wide query |
| `_query_combatant_info` | `analyze.py` | Talents/gear (same per run) |
| `_process_cast_events` | `analyze.py` | Parse events into spell counts |

### Functions Needing Adaptation

- `_query_damage_done`: Currently takes `start_time/end_time/source_id`. For dungeon-wide, pass the report's overall time range instead of a single fight's.
- `_query_buff_table`: Same -- widen time range.
- `_query_cast_events`: Same -- but beware pagination cost across the full run.
- `query_fight_info_full`: Need a new variant that queries ALL fights, not a single fight ID.

### New Code Needed

1. **`_query_all_fights`** -- Query `report.fights()` without fightIDs, return full list
2. **`_classify_fight_segments`** -- Separate boss fights (encounterID > 0) from trash
3. **`DungeonRunAnalysisResponse`** model -- New Pydantic model
4. **`analyze_dungeon_run`** orchestrator function
5. **MCP tool registration** in `server.py`

## Proposed Architecture

### New File: `src/tools/dungeon_analysis.py`

Following the project's one-tool-per-file pattern.

### Query Strategy (Rate-Limit Optimized)

**Tier 1 (always):** 3 queries, ~3-5 points total
1. `report.fights()` -- all fights (1 query)
2. `report.masterData` -- actors + abilities (already combined with fights in 1 query)
3. `report.table(DamageDone)` -- dungeon-wide damage with full time range (1 query)

**Tier 2 (default on):** +2-3 queries, ~3-5 points
4. `report.table(Buffs)` -- dungeon-wide buff uptimes (1 query)
5. `report.events(CombatantInfo)` -- talents/gear snapshot (1 query)
6. `report.events(Deaths)` -- death events across run (1 query)

**Tier 3 (optional, expensive):** +10-50 queries, ~15-75 points
7. `report.events(Casts)` -- full cast events across all segments for APL check
   - Only enable with explicit parameter flag
   - Paginated: expect 30-100 pages for a full M+ run

**Total budget: ~6-10 points (Tier 1+2), ~20-80 points (with Tier 3)**

### Output Structure

```python
class FightSegmentSummary(BaseModel):
    """M+ 副本中单个战斗段落摘要。"""
    fight_id: int
    name: str
    is_boss: bool
    duration_sec: float
    player_dps: float
    deaths: int

class DungeonRunAnalysisResponse(BaseModel):
    """analyze_dungeon_run 工具返回值。"""
    report_code: str
    player_name: str
    spec: str
    dungeon_name: str
    keystone_level: int = 0
    total_duration_sec: float
    total_dps: float
    total_damage: float
    total_deaths: int
    death_times: list[float] = []
    # Per-ability damage breakdown (top 15)
    damage_by_ability: list[dict] = []  # [{name, total, pct}]
    # Buff uptimes across run
    buff_uptimes: list[dict] = []  # [{name, uptime_pct}]
    # Per-segment breakdown
    segments: list[FightSegmentSummary] = []
    # Gear/talents (from CombatantInfo)
    item_level: float = 0.0
    player_talents: list[str] = []
    # Optional cast analysis (if enabled)
    spell_counts: dict[str, int] = {}
    active_time_pct: float = 0.0
    # Top issues summary
    top_issues: list[str] = []
```

### Tool Signature

```python
async def analyze_dungeon_run(
    client: WCLClient,
    report: str,
    player: str,
    spec: str,
    include_casts: bool = False,  # Tier 3 -- expensive
) -> DungeonRunAnalysisResponse:
```

**No difficulty parameter needed** -- M+ is always "mythic_plus", and the keystone level comes from the report data.

**No fight_id parameter** -- the whole point is analyzing the entire run.

## Rate Limit Budget

| Operation | Queries | Est. Points | Notes |
|-----------|---------|-------------|-------|
| fights + masterData | 1 (combined) | ~1 | Single query |
| table(DamageDone) whole run | 1 | ~1-2 | No pagination |
| table(Buffs) whole run | 1 | ~1-2 | No pagination |
| events(CombatantInfo) | 1 | ~1 | Small payload |
| events(Deaths) | 1 | ~1 | Small payload |
| **Tier 1+2 total** | **5** | **~5-7** | Safe |
| events(Casts) full run | 30-100 | ~30-100 | Pagination heavy |

**Recommendation:** Default to Tier 1+2 only (~5-7 points). Add `include_casts: bool = False` parameter to opt into full cast analysis. This keeps the base tool cheap enough to call multiple times per session.

## Common Pitfalls

### Pitfall 1: Pagination Explosion on Events
**What goes wrong:** Querying `events(Casts)` across a 30-minute M+ run returns thousands of events across 50-100 pages.
**How to avoid:** Default to `table()` for aggregates. Only use `events()` when explicitly requested via `include_casts=True`.

### Pitfall 2: Fight Time Ranges
**What goes wrong:** Using `startTime: 0, endTime: 99999999` may include pre-pull time or between-pull downtime in DPS calculations.
**How to avoid:** Use actual first fight startTime and last fight endTime from `report.fights()`. Calculate active time vs wall-clock time.

### Pitfall 3: sourceID Ambiguity in Long Runs
**What goes wrong:** Assuming sourceID is stable across the whole report (it is -- sourceID is report-scoped, not fight-scoped).
**How to avoid:** This is actually fine. Query masterData once, use sourceID for all queries.

### Pitfall 4: Empty/Partial Logs
**What goes wrong:** Report may contain incomplete data (disconnects, partial uploads).
**How to avoid:** Check fight count and total duration for sanity. Warn if < 3 fights or duration < 10 minutes.

### Pitfall 5: DPS Calculation -- Active Time vs Wall Time
**What goes wrong:** Dividing total damage by wall-clock time gives misleadingly low DPS (includes run-between-pulls time).
**How to avoid:** Sum individual fight durations for "active time". Report both overall DPS (wall clock) and active DPS (fight time only).

## Code Examples

### Query All Fights in a Report

```python
async def _query_all_fights(
    client: WCLClient, report_code: str
) -> list[dict]:
    """查询报告中所有战斗段落。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights {{
                    id
                    startTime
                    endTime
                    kill
                    encounterID
                    name
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    return report.get("fights", [])
```

### Dungeon-Wide Damage Table

```python
async def _query_dungeon_damage(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> dict:
    """查询整个副本的伤害分布表。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                table(
                    startTime: {start_time}
                    endTime: {end_time}
                    sourceID: {source_id}
                    dataType: DamageDone
                )
            }}
        }}
    """
    data = await client.query(gql)
    return data.get("reportData", {}).get("report", {}).get("table", {})
```

### Classify Segments

```python
def _classify_segments(fights: list[dict]) -> tuple[list[dict], list[dict]]:
    """分离 boss 战斗和小怪段落。"""
    bosses = [f for f in fights if f.get("encounterID", 0) > 0]
    trash = [f for f in fights if f.get("encounterID", 0) == 0]
    return bosses, trash
```

## Project Constraints (from CLAUDE.md)

- Code comments in Chinese with ASCII block separators
- File max 800 lines, function max 50 lines, max 3 nesting levels
- Simplicity first -- simplest working implementation
- Backward compatibility -- must not break existing tools
- Doc-Code isomorphism -- update L2/L3 docs when adding new file
- MCP stdio transport -- all logging to stderr

## Tool Registration Pattern (from server.py)

Follow existing pattern exactly:
1. Import function in `server.py`
2. Add `@mcp.tool()` decorated wrapper
3. Update module docstring tool list
4. Update `src/tools/CLAUDE.md` member list

## Sources

### Primary (HIGH confidence)
- Handoff doc (`wow-mcp-handoff-final.md`) -- M+ analysis requirements, WCL API patterns
- Codebase: `src/tools/analyze.py` -- existing single-fight analysis patterns
- Codebase: `src/wcl_client.py` -- query infrastructure
- Codebase: `src/models.py` -- Pydantic model patterns
- Codebase: `src/server.py` -- tool registration patterns

### Secondary (MEDIUM confidence)
- [WCL API Report docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/report.doc.html) -- table/fights parameters
- [WCL API ReportFight docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/reportfight.doc.html) -- fight fields including keystoneLevel
- [WCL forum: table issues](https://forums.combatlogforums.com/t/api-v2-issues-with-table-from-report/10722) -- startTime/endTime behavior

### Tertiary (LOW confidence)
- `keystoneLevel` and `dungeonPulls` fields on ReportFight -- referenced in community projects ([keystone-heroes](https://github.com/ljosberinn/keystone-heroes)) but not verified via live query. May need runtime discovery. Fallback: omit these fields initially, add when verified.
