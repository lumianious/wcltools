# Technology Stack: M+ Coaching Intelligence

**Project:** WoW Coach MCP Server — v2.0 M+ Milestone
**Researched:** 2026-03-28
**Focus:** WCL API queries, rate limit budget, integration with existing codebase

## Executive Summary

The M+ coaching milestone requires NO new dependencies. The existing WCL GraphQL client, cache, and Pydantic models handle everything. The core challenge is **query design**: how to efficiently query `characterRankings` for M+ dungeons, extract benchmark data from top-player reports, and stay within the 3600 points/hour rate limit when aggregating across multiple dungeons.

Key discovery: WCL `characterRankings` uses **difficulty: 10** for Mythic+ dungeons (vs 3/4/5 for Normal/Heroic/Mythic raid). The encounter ID for M+ is the **dungeon encounter ID** (the dungeon-as-a-whole boss entry, e.g. the encounter listed under the M+ zone in `worldData`). The `bracket` parameter filters by keystone level.

## WCL API: M+ Rankings Query Structure

### characterRankings for M+ Dungeons

**Confidence: MEDIUM** -- Synthesized from WCL API docs snippets, community projects, forum discussions, and v1 API patterns. Not verified via live query.

The same `worldData.encounter.characterRankings` query used for raids works for M+ dungeons with different parameters:

```graphql
worldData {
  encounter(id: DUNGEON_ENCOUNTER_ID) {
    name
    characterRankings(
      className: "Druid"
      specName: "Balance"
      metric: dps
      difficulty: 10          # M+ difficulty constant
      bracket: 12             # Optional: keystone level filter
      includeCombatantInfo: false
      page: 1
    )
  }
}
```

**Key differences from raid rankings:**

| Parameter | Raid Value | M+ Value | Notes |
|-----------|-----------|----------|-------|
| `difficulty` | 3/4/5 (Normal/Heroic/Mythic) | **10** | M+ difficulty constant |
| `bracket` | Item level ranges | **Keystone level** | Optional; omit for all keys |
| `metric` | `dps` | `dps` (also: `speed`, `execution`) | Use `dps` for benchmark building |
| `encounter_id` | Boss encounter ID | **Dungeon encounter ID** | The dungeon-level encounter, not individual bosses |

**Confidence on difficulty=10: MEDIUM.** Multiple indirect sources confirm this (v1 API docs mention "10 = Dungeon/Mythic+/CMs", community WCL wrappers use it, and WCL forum discussions reference it). Not verified via live GraphQL introspection.

### Rankings Response Structure

The `characterRankings` response for M+ is structurally identical to raids:

```json
{
  "rankings": [
    {
      "name": "PlayerName",
      "class": "Druid",
      "spec": "Balance",
      "amount": 285432.5,
      "duration": 1856000,
      "report": {
        "code": "abc123XYZ",
        "fightID": 5
      },
      "talents": [...],         // if includeCombatantInfo: true
      "gear": [...],            // if includeCombatantInfo: true
      "bracketData": 626        // ilvl
    }
  ],
  "page": 1,
  "hasMorePages": true,
  "count": 100
}
```

**Critical: `report.fightID` points to the dungeon aggregate fight** in the report, not individual boss pulls. This is the fight that covers the entire M+ run, with the full time span.

### Dungeon Encounter IDs

Dungeon encounter IDs are discovered via the existing `get_encounters` tool (`worldData.expansion.zones`). Each M+ dungeon zone has encounters listed -- for M+ rankings, use the **encounter ID that represents the whole dungeon**.

**How to find them:** Call `get_encounters(content_type="mythic_plus")` which returns zones with 1-2 encounters. The encounter ID from this zone is what you pass to `characterRankings`.

**Confidence: HIGH** -- This pattern is identical to how raids work, and the `get_encounters` tool already handles M+ zone discovery.

## Rate Limit Budget for M+ Benchmark Building

### Budget: 3600 points/hour

### Per-Tool Cost Estimates

#### M+ Benchmark Aggregation (NEW: `get_mplus_benchmarks`)

Building benchmarks requires sampling top players from rankings, then querying their reports for cast/buff data.

| Step | Query | Points/Query | Quantity | Subtotal |
|------|-------|-------------|----------|----------|
| 1. Rankings | `characterRankings(difficulty: 10)` | ~1-2 | 1 per dungeon | 1-2 |
| 2. masterData | `report.masterData` per unique report | ~1 | ~3-5 (reports are shared) | 3-5 |
| 3. Cast events | `report.events(Casts)` per player | ~1-3 per page | 5 players x 2-5 pages | 10-75 |
| 4. Buff table | `report.table(Buffs)` per player | ~1-2 | 5 players | 5-10 |
| **Total per dungeon** | | | | **~20-90** |

**For a single dungeon benchmark with 5 sample players: ~20-90 points.**
**For all 8 season dungeons: ~160-720 points** (nearly half the hourly budget).

### Rate Limit Strategy

| Strategy | Description | Recommended |
|----------|-------------|-------------|
| **Small sample size** | 5 players per dungeon (not 50 like raids) | YES -- M+ has less variance than raid fights |
| **Cache aggressively** | 6-hour TTL on benchmarks (same as raids) | YES |
| **One dungeon at a time** | Build benchmarks per-dungeon on demand, not all at once | YES |
| **Reuse report groups** | Group rankings by report code, query masterData once per report | YES (existing pattern from timelines.py) |
| **Skip detailed events for basic benchmark** | Use `table()` aggregates instead of full `events()` pagination | YES for rotation profile; NO for cooldown timelines |

### Recommended Sample Size: 5 Players

**Why 5 instead of 50 (like raid tools)?**
- M+ DPS variance is lower (same dungeon, similar pulls, no raid composition variance)
- Full event queries across a 20-30 min dungeon are expensive (2-5 pages per player)
- 5 players gives median/p25/p75 with acceptable accuracy
- Keeps per-dungeon cost at ~20-40 points (manageable)

### Budget Allocation Per Session

| Tool Call | Est. Points | Cacheable |
|-----------|-------------|-----------|
| `get_mplus_benchmarks` (1 dungeon) | 20-40 | YES (6h) |
| `get_mplus_cooldown_timeline` (1 dungeon) | 30-90 | YES (6h) |
| `analyze_dungeon_run` (player analysis) | 5-80 | NO |
| `get_mplus_defensive_patterns` (1 dungeon) | 20-40 | YES (6h) |
| **Typical session** (1 dungeon focus) | **75-250** | |

A typical coaching session focusing on one dungeon stays well within 3600 points/hour.

## New GraphQL Queries Needed

### Query 1: M+ Rankings (characterRankings with difficulty: 10)

```python
async def _query_mplus_rankings(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    sample_size: int = 5,
    bracket: int | None = None,
) -> tuple[str, list[dict]]:
    bracket_filter = f"bracket: {bracket}" if bracket else ""
    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "{class_name}"
                    specName: "{spec_name}"
                    metric: dps
                    difficulty: 10
                    includeCombatantInfo: false
                    {bracket_filter}
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql)
    encounter = data.get("worldData", {}).get("encounter", {})
    enc_name = encounter.get("name", "")
    cr = encounter.get("characterRankings", {})
    rankings = cr.get("rankings", [])[:sample_size]
    return enc_name, rankings
```

**This is the ONLY new query pattern needed.** All other queries (masterData, events, table) already exist and work identically for M+ reports as for raid reports.

### Query 2: Events Across Full Dungeon Run (Already Exists)

The existing `_query_cast_events` from `timelines.py` works for M+ by using the aggregate fight's time range. The `report.fightID` from rankings points to the dungeon aggregate fight.

**No changes needed** -- pass `fightID` from rankings directly.

### Query 3: Buff/Damage Tables (Already Exists)

`_query_buff_table` and `_query_damage_done` from `rotation.py` / `analyze.py` work unchanged.

## Integration with Existing Codebase

### What to Reuse (NO modification needed)

| Component | Location | Reuse Pattern |
|-----------|----------|---------------|
| `WCLClient.query()` | `wcl_client.py` | Direct -- same GraphQL endpoint |
| `_query_master_data()` | `rotation.py` | Direct -- same report structure |
| `_query_cast_events()` | `timelines.py` | Direct -- pass M+ fight aggregate fightID |
| `_query_buff_table()` | `rotation.py` | Direct |
| `_query_damage_events()` | `timelines.py` | Direct |
| `cache_get/cache_set` | `cache.py` | Direct -- same TTL pattern |
| `SPEC_MAPPING` | `builds.py` | Direct |
| `get_spec_spells()` | `data.py` | Direct -- spell data is spec-level, not content-level |
| Clustering (`_cluster_timestamps`) | `timelines.py` | Direct -- works on any timestamp list |

### What to Extend

| Component | Change | Why |
|-----------|--------|-----|
| `DIFFICULTY_MAP` in `builds.py` | Add `"mythic_plus": 10` | New difficulty constant for M+ queries |
| `_query_rankings` pattern | New function with `difficulty: 10` | Cannot reuse raid rankings function (hardcoded difficulty param) |
| `get_encounters` | Already supports `content_type="mythic_plus"` | Use to discover dungeon encounter IDs |

### What NOT to Add

| Anti-Pattern | Why Avoid |
|--------------|-----------|
| Separate M+ WCL client | Same API, same auth, same rate limits |
| New cache backend | File-based JSON cache works fine at this scale |
| New Pydantic models for every response variant | Reuse existing models where possible; M+ rotation profile is structurally identical to raid rotation profile |
| Batch all-dungeon benchmark building | Too expensive (160-720 points); build per-dungeon on demand |
| Real-time rate limit gating | Existing tracking + conservative sample sizes are sufficient |

## Recommended Stack (No Changes)

### Core (Unchanged)

| Technology | Version | Purpose | Status |
|------------|---------|---------|--------|
| Python | 3.12+ | Runtime | Unchanged |
| mcp | >=1.25,<2 | MCP SDK | Unchanged |
| httpx | Latest | HTTP client for WCL API | Unchanged |
| Pydantic v2 | Latest | Data models | Unchanged |

### No New Dependencies Needed

The M+ milestone is purely additive: new tool functions using existing infrastructure. No new libraries, no new services, no new data stores.

## Difficulty Constants (Extended)

```python
# Current (builds.py)
DIFFICULTY_MAP = {
    "normal": 3,
    "heroic": 4,
    "mythic": 5,
}

# Extended for M+
DIFFICULTY_MAP = {
    "normal": 3,
    "heroic": 4,
    "mythic": 5,
    "mythic_plus": 10,  # M+ dungeons
}
```

## Key API Behavior Differences: M+ vs Raid

| Behavior | Raid | M+ |
|----------|------|----|
| Rankings `difficulty` | 3/4/5 | 10 |
| Rankings `bracket` | Item level | Keystone level |
| Report fight structure | Single boss fight | Aggregate fight (full dungeon) + segment fights |
| `fightID` from rankings | Points to boss kill fight | Points to dungeon aggregate fight |
| Events time range | ~3-10 min (boss fight) | ~15-35 min (full dungeon) |
| Events pagination depth | 1-3 pages per player | 3-10+ pages per player |
| Active time calculation | = fight duration | = sum of segment durations (excludes between-pull downtime) |
| Encounter ID source | Boss encounter ID from zone | Dungeon encounter ID from M+ zone |

## Verification Needed Before Implementation

These items should be verified with a live API call during implementation:

1. **Confirm `difficulty: 10` works** with `characterRankings` for M+ dungeons in the v2 GraphQL API. Fallback: try without difficulty parameter (defaults to highest difficulty).

2. **Confirm `bracket` accepts keystone level as integer**. E.g., `bracket: 12` for +12 keys. Fallback: omit bracket to get all key levels.

3. **Confirm `fightID` from M+ rankings** points to the aggregate dungeon fight (not a segment). Validate by checking the fight's `encounterID > 0` and time range spans the full dungeon.

4. **Confirm dungeon encounter IDs** from `get_encounters(content_type="mythic_plus")` work with `characterRankings`. If M+ zones have no encounters listed, investigate the M+ season zone structure.

## Sources

### Primary (HIGH confidence)
- Existing codebase: `src/tools/timelines.py`, `src/tools/builds.py`, `src/tools/rotation.py`, `src/tools/dungeon_analysis.py`
- Existing codebase: `src/wcl_client.py` (query infrastructure)
- Existing codebase: `scripts/export_talent_data.py` (characterRankings usage patterns)
- Previous research: `.planning/quick/260328-l78-build-analyze-dungeon-run-tool-aggregate/260328-l78-RESEARCH.md`

### Secondary (MEDIUM confidence)
- [WCL API Encounter docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/encounter.doc.html) -- characterRankings parameters
- [WCL API Difficulty docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/difficulty.doc.html) -- difficulty type structure
- [WCL API Character docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/character.doc.html) -- encounterRankings, zoneRankings
- [WCL Rankings guide](https://www.warcraftlogs.com/help/ranks/) -- M+ bracket = keystone level, scoring mechanics
- [Archon Rankings guide](https://www.archon.gg/wow/articles/help/rankings-and-parses) -- M+ All Star = Blizzard dungeon rating
- [WCL M+ Rankings Discussion](https://forums.combatlogforums.com/t/mythic-dungeons-rankings-discussion/662) -- keystone level partitioning, metric options

### Tertiary (LOW confidence)
- WCL v1 API pattern showing difficulty=10 for Mythic+/Dungeons/CMs -- inferred from community wrapper packages and search results, not directly verified in v2 GraphQL schema
- `bracket` parameter accepting keystone integer -- inferred from "brackets are keystone levels for M+ dungeons" in docs, not verified via live query
