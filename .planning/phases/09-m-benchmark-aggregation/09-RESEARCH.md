# Phase 9: M+ Benchmark Aggregation - Research

**Researched:** 2026-03-28
**Domain:** WCL GraphQL API data extraction, M+ segment aggregation, benchmark pipeline
**Confidence:** HIGH

## Summary

Phase 9 builds a benchmark aggregation pipeline that collects M+ performance data from top players per dungeon. The pipeline reuses the established rankings-to-reports-to-events pattern from `timelines.py`, adapted for M+ segment structure (boss-bounded). The core challenge is extracting 4 data types (damage breakdown, CD casts, defensive CDs, interrupts) across multiple segments from 5 reports, while staying within WCL API rate limits (~25-35 points per benchmark).

The existing codebase provides all building blocks: `query_mplus_rankings` for getting top player report codes, `_query_all_fights` for segment discovery, `_classify_segments` for boss/trash classification, and `_query_cast_events`/`_query_damage_events` for event extraction. The new code is primarily orchestration and aggregation logic.

**Primary recommendation:** Build a single `mplus_benchmarks.py` module in `src/tools/` that orchestrates the pipeline, with Pydantic models in `models.py` and one new MCP tool registered in `server.py`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Reuse the timelines.py pipeline pattern (rankings -> reports -> events -> aggregate -> cache) adapted for M+ segment structure
- Query all 5 top-player reports in parallel with asyncio.Semaphore(3) to balance latency vs rate limit safety
- Cache the final aggregate only (not per-report raw data) -- cache key `mplus_bench:{spec}:{encounter_id}:k{level}:segments`, one object per dungeon benchmark
- Align segments by boss-bounded positions (trash-before-B1, B1, trash-B1->B2, B2, etc.) to sidestep route differences across top players
- Per trash segment: spell damage % + top-N spell breakdown + major CD casts + defensive CDs + interrupt count -- covers BENCH-02, CD-01, DMG-01, SURV-01, INT-01 in one collection pass
- "Major CD" = CD >= 30s, tagged dps/defensive/raid_cd -- consistent with timelines.py criteria and existing spell data
- Boss encounters: cast-level benchmarks (reuse cast-sequence/rotation patterns from raid tools) -- per BENCH-03
- Aggregation across 5 players: use median of per-segment metrics, aligned by boss-bounded segment position
- Single MCP tool `get_mplus_benchmarks` returns full dungeon benchmark bundle (all segments, all data types)
- Parameters: `spec, encounter_id, key_level` -- matches Phase 8's `query_mplus_rankings` signature
- Response: hierarchical -- segments[] with {damage_breakdown, cd_casts, defensive_cds, interrupts}, plus separate boss_benchmarks[] with cast-level data
- Lazy fetch on first request, cache 6h (matches raid TTL and Phase 8 rankings cache). Budget: ~25-35 WCL points per dungeon benchmark (5 reports x 5-7 queries)

### Claude's Discretion
- Internal module structure (single file vs split by concern)
- Exact Pydantic model field names (follow existing naming conventions)
- Error handling for reports with missing segment data
- Top-N spell count for damage breakdown

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BENCH-02 | Per-trash-segment spell damage % and major CD timing from top players | Damage events via `dataType: DamageDone` per segment time range; CD casts via `dataType: Casts` filtered by tracked spell IDs from `get_spec_spells` |
| BENCH-03 | Cast-level data for boss encounters within M+ dungeons | Reuse `_query_cast_events` pattern from `timelines.py` for boss fight IDs; boss fights identified by `encounterID > 0` |
| CD-01 | Major CD usage (offensive/defensive/pots) across boss-bounded trash segments | `_build_tracked_spells` from `timelines.py` provides CD >= 30s filter with dps/defensive/raid_cd tags |
| CD-02 | CD spacing pattern across full dungeon -- which trash segment gets which CDs | Aggregate CD cast timestamps per segment, show which CDs appear in which segment positions |
| DMG-01 | Per-trash-segment spell damage % distribution | `report.table(DamageDone, startTime, endTime, sourceID)` per segment -- same pattern as `_query_dungeon_damage_table` |
| SURV-01 | Defensive CD usage patterns from top M+ players | Filter cast events for spells tagged "defensive" or "raid_cd" -- same tag system as `_infer_ability_type` |
| INT-01 | Interrupt cast counts and targets per dungeon segment | WCL `dataType: Interrupts` event type exists; alternatively filter `dataType: Casts` for known interrupt spell IDs |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | >=2.0 (installed) | Data models for benchmark response | Already used across all models |
| asyncio | stdlib | Parallel report fetching with Semaphore(3) | Already used in dungeon_analysis.py |
| statistics | stdlib | Median aggregation across players | Already used in timelines.py and mplus_rankings.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| collections.defaultdict | stdlib | Grouping events by segment/spell | Same pattern as timelines.py |
| logging | stdlib | Debug/info logging | All existing tools use it |

No new external dependencies required. Everything builds on installed packages.

## Architecture Patterns

### Recommended Module Structure
```
src/
  tools/
    mplus_benchmarks.py    # Pipeline: rankings -> reports -> segments -> aggregate -> cache
  models.py                # New Pydantic models (append to existing)
  server.py                # Register get_mplus_benchmarks tool
```

Single file `mplus_benchmarks.py` is appropriate (estimated ~400-500 lines). The pipeline is linear and self-contained. Splitting would add import complexity without benefit.

### Pattern 1: Rankings-to-Reports Pipeline (from timelines.py)
**What:** Fetch rankings, extract report codes, query per-report data, aggregate, cache
**When to use:** All benchmark tools
**Example:**
```python
# Source: src/tools/timelines.py pattern
async def get_mplus_benchmarks(client, spec, encounter_id, key_level):
    # 1. Cache check
    cached = cache_get(cache_key, CACHE_TTL)
    if cached: return MplusBenchmarkResponse(**cached)

    # 2. Get top player report codes from Phase 8
    meta, entries = await query_mplus_rankings(client, encounter_id, spec, key_level)

    # 3. Fetch per-report segment data in parallel (semaphore-limited)
    sem = asyncio.Semaphore(3)
    tasks = [_fetch_report_segments(client, sem, entry) for entry in entries]
    report_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 4. Align segments by boss-bounded position, aggregate with median
    # 5. Cache and return
```

### Pattern 2: Boss-Bounded Segment Alignment
**What:** Use boss encounters as stable position markers across different player reports
**When to use:** Aligning segments from different players who may have taken different routes
**Example:**
```python
# Source: src/tools/dungeon_analysis.py pattern
# Each report's fights are classified: encounterID > 0 = boss, else = trash
# Segment positions: trash-before-B1 (pos 0), B1 (pos 1), trash-B1-B2 (pos 2), B2 (pos 3), etc.
# Different routes produce different trash fight counts per segment,
# but the boss-bounded position is stable
def _assign_segment_positions(fights: list[dict]) -> list[tuple[int, str, dict]]:
    """Returns [(position, type, fight_data), ...] sorted by time."""
    bosses, trash = _classify_segments(fights)
    # Sort all fights by startTime, assign position based on boss boundaries
```

### Pattern 3: Per-Report Data Collection (multi-query)
**What:** For each report, fetch fights + masterData + events in minimal queries
**When to use:** When extracting multiple data types from a single report
**Example:**
```python
async def _fetch_report_segments(client, sem, entry):
    async with sem:
        report_code = entry.report_code
        fight_id = entry.fight_id

        # 1. Get all fights + masterData (2 queries, can be parallel)
        fights, _ = await _query_all_fights(client, report_code)
        actors = await _query_master_data_from_timelines(client, report_code)
        source_id = _find_actor_id(actors, entry.name)

        # 2. Get segment fights for this dungeon run
        runs = _group_fights_by_dungeon(fights)
        # Select the run matching the ranking's fight_id

        # 3. Per segment: damage table + cast events (batch)
        # Minimize queries by using time ranges spanning whole segments
```

### Pattern 4: WCL Interrupt Events
**What:** Query interrupt events using `dataType: Interrupts` or filter Casts for known interrupt spell IDs
**When to use:** INT-01 requirement
**Details:**
```python
# Option A: Direct interrupt events (preferred if WCL supports it well)
# dataType: Interrupts returns events where the player interrupted a cast
gql = f"""
    reportData {{
        report(code: "{code}") {{
            events(
                startTime: {start}
                endTime: {end}
                fightIDs: [{fight_ids}]
                dataType: Interrupts
                sourceID: {source_id}
                limit: 10000
            ) {{ data nextPageTimestamp }}
        }}
    }}
"""

# Option B: Filter Casts for known interrupt spell IDs
# Each class has one interrupt: Pummel(6552), Kick(1766), Mind Freeze(47528), etc.
# This approach needs a hardcoded map of interrupt spell IDs per class
```

**Recommendation:** Use `dataType: Interrupts` directly. It is a first-class WCL EventDataType and returns exactly what we need: successful interrupts with the interrupted ability info. This avoids maintaining a hardcoded interrupt spell ID list.

### Anti-Patterns to Avoid
- **Fetching all events for the entire dungeon in one query:** Too much data. Query per segment time range instead.
- **Caching per-report intermediate data:** Decision says cache only final aggregate. Per-report caching wastes disk and complicates invalidation.
- **Using wall-clock fight ordering instead of boss-bounded positions:** Different routes mean different trash pull order. Boss positions are the stable alignment points.
- **Querying with `fightIDs` for multiple fights across different segments:** Use `startTime/endTime` ranges per segment instead, which allows batching adjacent trash pulls into one segment query.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| M+ rankings fetching | Custom rankings query | `query_mplus_rankings` from `mplus_rankings.py` | Handles bracket fallback, caching, parsing |
| Fight segment discovery | Custom fight list parsing | `_query_all_fights` + `_group_fights_by_dungeon` + `_classify_segments` from `dungeon_analysis.py` | Already handles gameZone grouping, segment classification |
| Tracked spell filtering | Custom CD filtering | `_build_tracked_spells` from `timelines.py` | CD >= 30s filter with proper tag handling |
| Actor ID resolution | Custom player matching | `_find_actor_id` from `timelines.py` or `find_actor_id_ci` from `_wcl_helpers.py` | Case-insensitive matching, already tested |
| Spell name lookup | Hardcoded names | `get_spell_name` from `data/__init__.py` | Chinese spell names, talent spell coverage |
| File caching | Custom cache | `cache_get`/`cache_set` from `cache.py` | TTL-based JSON file cache, already standardized |
| Spec parsing | Custom parser | `_parse_spec` from `mplus_rankings.py` | Uses SPEC_MAPPING with full 39-spec coverage |

**Key insight:** Phase 9 is ~70% orchestration of existing building blocks. The novel code is (1) boss-bounded segment alignment across reports, (2) per-segment event aggregation, and (3) median-based cross-player stats.

## Common Pitfalls

### Pitfall 1: Fight ID Mismatch Between Rankings and Report Fights
**What goes wrong:** The `fight_id` from `MplusRankingEntry` refers to the overall dungeon encounter fight (encounterID > 0), not individual segment fights. The segment fights have their own IDs.
**Why it happens:** WCL M+ rankings point to the dungeon-level fight. The actual segment fights (trash pulls, individual bosses) are separate fight entries within the same report.
**How to avoid:** Use `fight_id` from rankings to identify which dungeon run in the report to analyze, then use `_query_all_fights` to get all segment fights for that run. Match by gameZone or by time overlap with the ranked fight.
**Warning signs:** Getting empty event data when querying with the rankings fight_id for segment-level data.

### Pitfall 2: Segment Count Mismatch Across Reports
**What goes wrong:** Different top players may have different numbers of trash segments (different routes, different pull counts). Simple index-based alignment breaks.
**Why it happens:** WCL records each pull as a separate fight. A player who does 3 trash pulls before Boss 1 has 3 trash fights; another who does 2 big pulls has 2.
**How to avoid:** Use boss encounters as segment boundaries. Merge all trash fights between two bosses into a single logical segment. Aggregate damage/CD data across the merged trash pulls.
**Warning signs:** Getting different segment counts from different reports; median calculation failing on misaligned arrays.

### Pitfall 3: Rate Limit Budget Overshoot
**What goes wrong:** Querying 5 reports with 7+ queries each can exceed 35 WCL points, especially if pagination kicks in for large trash segments.
**Why it happens:** Each `events()` query costs 1+ points. Pagination doubles the cost. A dungeon with 15+ trash segments means 15+ damage table queries per report.
**How to avoid:** Use `report.table(DamageDone)` with broad startTime/endTime per logical segment (merged trash) instead of per-individual-fight. This reduces queries from ~15 to ~6-8 per report. Also, use `asyncio.Semaphore(3)` to prevent burst.
**Warning signs:** WCL rate limit warnings in logs; points_remaining dropping faster than expected.

### Pitfall 4: Missing Players in Report masterData
**What goes wrong:** Player name from rankings might not match any actor in the report's masterData.
**Why it happens:** Name/server mismatches, character transfers, report corruption. Rankings store `name` but masterData has `name` + `server`.
**How to avoid:** Use case-insensitive matching (`find_actor_id_ci`). If no match found, skip that report and note it in logs. The 5-player sample gives tolerance for 1-2 failures.
**Warning signs:** `source_id is None` for a player; fewer than 3 players contributing to aggregation.

### Pitfall 5: Interrupt Data May Be Empty for Some Specs
**What goes wrong:** Some DPS specs in M+ may not interrupt at all in certain segments, leading to 0 interrupt counts that are valid data, not errors.
**Why it happens:** Interrupt responsibility often falls on specific players in the group; the top DPS player may not be the primary interrupter.
**How to avoid:** Report interrupt count as-is (including 0). The benchmark is "what do top players do", even if that means 0 interrupts in a segment. Note: this is still useful because non-zero counts indicate high-priority interrupt segments.
**Warning signs:** All 5 players showing 0 interrupts in segments where interrupts are known to be important.

### Pitfall 6: Boss Fights Within M+ Have No encounterID in Segments
**What goes wrong:** In WCL M+ reports, individual boss fights within the dungeon may have `encounterID: 0` just like trash segments. Only the overall dungeon completion fight has `encounterID > 0`.
**Why it happens:** WCL M+ fight structure uses `encounterID > 0` for the overall dungeon encounter, while individual boss pulls within the dungeon are recorded as regular fights with boss names but `encounterID: 0`.
**How to avoid:** Use the fight `name` field to identify boss fights within M+ dungeons (e.g., "Forgemaster Garfrost"). Cross-reference with the dungeon's known boss list if available. The test data in `test_dungeon_analysis.py` shows this pattern: all segment fights have `encounterID: 0`.
**Warning signs:** `_classify_segments` returning all fights as "trash" with no bosses identified.

## Code Examples

### Example 1: Fetching Segment Damage Table
```python
# Source: src/tools/dungeon_analysis.py pattern
# Per-segment damage breakdown using report.table(DamageDone)
async def _query_segment_damage(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """Query damage table for a single segment time range."""
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
    table = data.get("reportData", {}).get("report", {}).get("table", {})
    entries = table.get("data", {}).get("entries", [])
    total = sum(e.get("total", 0) for e in entries)
    # Return top-N with percentages
    sorted_entries = sorted(entries, key=lambda e: e.get("total", 0), reverse=True)
    return [
        {"name": e.get("name", ""), "total": e.get("total", 0),
         "pct": round(e.get("total", 0) / total * 100, 1) if total > 0 else 0.0}
        for e in sorted_entries[:10]
    ]
```

### Example 2: Interrupt Event Query
```python
# Source: WCL API EventDataType documentation
async def _query_interrupt_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """Query interrupt events for a player in a time range."""
    all_events: list[dict] = []
    next_ts: int | None = start_time
    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(
                        startTime: {next_ts}
                        endTime: {end_time}
                        dataType: Interrupts
                        sourceID: {source_id}
                        limit: 10000
                    ) {{ data nextPageTimestamp }}
                }}
            }}
        """
        data = await client.query(gql)
        events_block = data.get("reportData", {}).get("report", {}).get("events", {})
        all_events.extend(events_block.get("data", []))
        next_ts = events_block.get("nextPageTimestamp")
    return all_events
```

### Example 3: Semaphore-Limited Parallel Fetching
```python
# Source: established asyncio pattern, matches CONTEXT.md decision
async def _fetch_all_reports(
    client: WCLClient,
    entries: list[MplusRankingEntry],
    spec: str,
) -> list[dict | None]:
    """Fetch segment data from all reports, limited to 3 concurrent."""
    sem = asyncio.Semaphore(3)

    async def _fetch_one(entry: MplusRankingEntry) -> dict | None:
        async with sem:
            try:
                return await _fetch_report_benchmark_data(client, entry, spec)
            except Exception as exc:
                logger.warning("Report fetch failed %s: %s", entry.report_code, exc)
                return None

    results = await asyncio.gather(*[_fetch_one(e) for e in entries])
    return [r for r in results if r is not None]
```

### Example 4: Boss-Bounded Segment Alignment
```python
# Source: derived from dungeon_analysis.py segment classification
def _build_segment_positions(fights: list[dict]) -> list[dict]:
    """
    Assign boss-bounded positions to fights.

    Position scheme: 0=trash-before-B1, 1=B1, 2=trash-B1-B2, 3=B2, etc.
    All trash fights between two bosses share the same position.
    """
    sorted_fights = sorted(fights, key=lambda f: f["startTime"])
    segments: list[dict] = []
    position = 0
    current_trash_start = None
    current_trash_end = None

    for f in sorted_fights:
        is_boss = _is_boss_fight(f)  # name-based or encounterID-based
        if is_boss:
            # Flush any accumulated trash
            if current_trash_start is not None:
                segments.append({
                    "position": position, "type": "trash",
                    "start_time": current_trash_start, "end_time": current_trash_end
                })
                position += 1
                current_trash_start = None
            # Add boss
            segments.append({
                "position": position, "type": "boss",
                "name": f.get("name", ""), "fight": f,
                "start_time": f["startTime"], "end_time": f["endTime"]
            })
            position += 1
        else:
            # Accumulate trash
            if current_trash_start is None:
                current_trash_start = f["startTime"]
            current_trash_end = f["endTime"]

    # Final trash segment after last boss
    if current_trash_start is not None:
        segments.append({
            "position": position, "type": "trash",
            "start_time": current_trash_start, "end_time": current_trash_end
        })

    return segments
```

### Example 5: Pydantic Model Structure
```python
# Source: existing models.py patterns
class SegmentDamageBreakdown(BaseModel):
    """Per-segment spell damage distribution."""
    spell_name: str
    total_damage: float = 0.0
    damage_pct: float = 0.0

class SegmentCDCast(BaseModel):
    """A major CD cast within a segment."""
    spell_name: str
    spell_id: int
    cast_count_median: float = 0.0
    ability_type: str = ""  # "offensive", "defensive", "buff"

class MplusBenchmarkSegment(BaseModel):
    """Benchmark data for one boss-bounded segment."""
    position: int
    segment_type: str = ""  # "trash" or "boss"
    segment_name: str = ""
    duration_median: float = 0.0
    damage_breakdown: list[SegmentDamageBreakdown] = Field(default_factory=list)
    cd_casts: list[SegmentCDCast] = Field(default_factory=list)
    defensive_cds: list[SegmentCDCast] = Field(default_factory=list)
    interrupt_count_median: float = 0.0

class MplusBenchmarkResponse(BaseModel):
    """get_mplus_benchmarks tool response -- full dungeon benchmark bundle."""
    spec: str
    encounter_id: int
    encounter_name: str = ""
    key_level: int = 0
    actual_bracket: int = 0
    sample_size: int = 0
    median_dps: float = 0.0
    segments: list[MplusBenchmarkSegment] = Field(default_factory=list)
    boss_benchmarks: list[...] = Field(default_factory=list)  # cast-level boss data
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-trash-pack matching | Boss-bounded segments | v2.0 design | Eliminates route dependency |
| Individual fight queries | Time-range table queries | Existing pattern | Reduces WCL point cost |
| Global damage aggregation | Per-segment breakdown | Phase 9 new | Enables segment-level coaching |

## Open Questions

1. **Boss fight identification within M+ segments**
   - What we know: Test data shows all segment fights have `encounterID: 0`. The overall dungeon completion has `encounterID > 0`. Real WCL data may differ.
   - What's unclear: Whether real M+ reports mark individual boss fights with `encounterID > 0` or just by name. This affects how we reliably distinguish boss from trash.
   - Recommendation: Check during implementation with a real M+ report. If bosses have `encounterID: 0`, use fight `name` matching against a known boss list (can be derived from the dungeon's encounters via `get_encounters`). The `aggregate_fight` in `DungeonRun` (with `encounterID > 0`) is the overall dungeon, not individual bosses.

2. **WCL `dataType: Interrupts` event structure**
   - What we know: `Interrupts` is a valid WCL EventDataType. The exact event fields (interrupted spell ID, target, etc.) need verification.
   - What's unclear: Exact payload shape of interrupt events.
   - Recommendation: During implementation, log a sample interrupt event from a real M+ report to confirm field names. For the initial build, count events per segment (interrupt count) which requires only event counting, not field parsing. Confidence: MEDIUM (WCL event structure is consistent across types).

3. **Identifying which dungeon run in a report matches the ranking**
   - What we know: Rankings give `report_code` and `fight_id`. The report may contain multiple dungeon runs.
   - What's unclear: Whether `fight_id` from rankings reliably matches the `aggregate_fight` (overall dungeon fight) or a segment fight.
   - Recommendation: Match by checking which `DungeonRun`'s aggregate_fight has the same `id` as the ranking's `fight_id`. If no aggregate_fight match, fall back to finding the run whose time range contains the ranking's fight.

## Project Constraints (from CLAUDE.md)

From global CLAUDE.md:
- Code comments in Chinese with ASCII block separators
- File: max 800 lines, Function: max 50 lines, max 3 nesting levels
- Directory: max 8 files per level (src/tools/ currently has 17 files -- already exceeded; adding one more is acceptable)
- Three-Question Filter before changes: Real need? Simpler approach? What breaks?
- Backward Compatibility: changes must not break existing functionality
- Doc-Code Isomorphism: code changes MUST update docs (L1/L2/L3)
- Simplicity First: simplest working implementation first

From parent CLAUDE.md (DocOps protocol):
- L2 update required for `src/tools/CLAUDE.md` when adding new file
- L3 header required for new `mplus_benchmarks.py`
- L2 update for `src/models.py` when adding new models
- L1 update if tool list changes in project root

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 + pytest-asyncio >=0.24 |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_mplus_benchmarks.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BENCH-02 | Per-trash-segment damage % and CD timing extraction | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_segment_damage_breakdown -x` | No -- Wave 0 |
| BENCH-03 | Cast-level boss benchmarks | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_boss_cast_benchmarks -x` | No -- Wave 0 |
| CD-01 | Major CD usage across segments | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_segment_cd_casts -x` | No -- Wave 0 |
| CD-02 | CD spacing pattern across dungeon | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_cd_spacing_pattern -x` | No -- Wave 0 |
| DMG-01 | Per-segment spell damage % | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_damage_pct_distribution -x` | No -- Wave 0 |
| SURV-01 | Defensive CD usage patterns | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_defensive_cd_patterns -x` | No -- Wave 0 |
| INT-01 | Interrupt counts per segment | unit | `uv run pytest tests/test_mplus_benchmarks.py::test_interrupt_counts -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_mplus_benchmarks.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_mplus_benchmarks.py` -- covers all 7 requirements
- [ ] `tests/fixtures/wcl_responses.py` -- add M+ report mock data (fights with segments, damage tables, cast events, interrupt events)
- [ ] Framework install: already configured in pyproject.toml

## Sources

### Primary (HIGH confidence)
- `src/tools/timelines.py` -- pipeline pattern: rankings -> reports -> masterData -> events -> aggregate -> cache
- `src/tools/mplus_rankings.py` -- `query_mplus_rankings` with bracket fallback and caching
- `src/tools/dungeon_analysis.py` -- `_query_all_fights`, `_group_fights_by_dungeon`, `_classify_segments` for M+ segment handling
- `src/models.py` -- all existing Pydantic models, naming conventions
- `src/cache.py` -- `cache_get`/`cache_set` with TTL
- `src/data/__init__.py` -- `get_spec_spells`, `get_spell_name` for spell metadata
- `src/tools/timelines.py:_build_tracked_spells` -- CD >= 30s filtering with tag classification

### Secondary (MEDIUM confidence)
- [WCL EventDataType documentation](https://www.warcraftlogs.com/v2-api-docs/warcraft/eventdatatype.doc.html) -- `Interrupts` is a valid dataType enum value
- [WCL Report API](https://www.warcraftlogs.com/v2-api-docs/warcraft/report.doc.html) -- events and table query structure

### Tertiary (LOW confidence)
- Interrupt event payload shape -- inferred from other WCL event types but not directly verified with live data

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all existing libraries
- Architecture: HIGH -- pipeline pattern directly reuses timelines.py + dungeon_analysis.py patterns
- Pitfalls: HIGH -- based on detailed code reading of existing M+ handling code
- WCL Interrupts API: MEDIUM -- EventDataType confirmed, but exact event fields unverified

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable -- WCL API v2 is mature, codebase patterns well-established)
