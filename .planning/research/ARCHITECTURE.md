# Architecture Patterns: M+ Coaching Intelligence Integration

**Domain:** M+ coaching tools for existing WoW Coach MCP Server
**Researched:** 2026-03-28
**Overall confidence:** HIGH (existing codebase patterns are well-understood; WCL M+ API specifics are MEDIUM)

## Recommended Architecture

### Principle: Reuse Raid Pipeline, Adapt for M+ Topology

The existing raid coaching tools follow a clean pipeline: `rankings -> reports -> events -> aggregate -> cache`. M+ tools should reuse this exact pipeline with one key adaptation: **M+ encounters ARE dungeon encounters in WCL** (each dungeon is an encounter ID), but they use `difficulty: 10` (Mythic Keystone) instead of 3/4/5, and optionally filter by `bracket` (key level).

This means the existing `_query_rankings` functions in `timelines.py`, `rotation.py`, `builds.py`, and `defensives.py` can be reused almost directly -- they just need M+ difficulty support added to `DIFFICULTY_MAP`.

### Component Boundaries

| Component | Status | Responsibility | Communicates With |
|-----------|--------|---------------|-------------------|
| `builds.py` DIFFICULTY_MAP | **MODIFY** | Add `"mythic_plus": 10` entry | All benchmark tools |
| `_mplus_benchmarks.py` (NEW) | **NEW** | M+ benchmark orchestration + aggregation across segments | timelines, rotation, defensives, cache |
| `_mplus_comparisons.py` (NEW) | **NEW** | M+ gap analysis (per-segment + full-run comparisons) | _analysis_comparisons (reuse), _analysis_metrics |
| `mplus_coach.py` (NEW) | **NEW** | Top-level M+ coaching tool (orchestrates analysis + benchmarks) | dungeon_analysis, _mplus_benchmarks, _mplus_comparisons |
| `dungeon_analysis.py` | **MODIFY** | Add death correlation data to segments | (existing consumers) |
| `cache.py` | **NO CHANGE** | Same cache, different keys | All tools |
| `models.py` | **MODIFY** | New M+ benchmark + coaching response models | New tools |
| `server.py` | **MODIFY** | Register new M+ coaching tools | New tools |

### Architecture Diagram

```
                    Claude (MCP Client)
                           |
                    server.py (tool registration)
                           |
            +--------------+--------------+
            |              |              |
   analyze_dungeon_run  mplus_coach   (existing raid tools)
    (existing, minor      (NEW)
     enhancements)          |
                    +-------+-------+
                    |               |
           _mplus_benchmarks   _mplus_comparisons
                (NEW)              (NEW)
                    |               |
            +---+---+---+     _analysis_comparisons
            |   |   |   |     _analysis_metrics
            |   |   |   |        (REUSE)
         timelines rotation defensives builds
            (existing benchmark tools, reused with difficulty=10)
```

## Question 1: Cache Strategy

**Decision: Same cache, namespaced keys. Do NOT create separate cache infrastructure.**

The existing `cache.py` is key-string-based with SHA256 hashing. M+ benchmarks naturally namespace themselves through the cache key string:

```python
# Raid benchmark (existing):
cache_key = f"cooldown_timelines_{spec}_{encounter_id}_heroic_50"

# M+ benchmark (new) -- encounter_id IS the dungeon encounter ID:
cache_key = f"cooldown_timelines_{spec}_{encounter_id}_mythic_plus_50"
```

The `difficulty` parameter already appears in cache keys for all benchmark tools. Adding `"mythic_plus"` as a valid difficulty value is sufficient -- no cache architecture changes needed.

**Rationale:**
- File-based cache with TTL works identically for M+ data
- 6-hour TTL is appropriate (M+ meta shifts slowly, same as raid)
- Key collision is impossible because difficulty string differs
- No benefit to separate cache directories -- adds complexity for zero gain

## Question 2: M+ Benchmark Pipeline

**Decision: Reuse existing benchmark tools directly. The "M+ benchmark" is just calling the same tools with `difficulty="mythic_plus"`.**

### Pipeline Flow

```
1. get_encounters(content_type="mythic_plus")
   -> Returns dungeon zones, each with encounter IDs (one per dungeon)

2. For each dungeon encounter_id:
   a. get_cooldown_timelines(spec, encounter_id, difficulty="mythic_plus")
      -> Reuses existing rankings->reports->events->cluster pipeline
      -> WCL returns M+ top players via characterRankings(difficulty: 10)
      -> Cast data spans FULL dungeon run (all segments)

   b. get_rotation_profile(spec, encounter_id, difficulty="mythic_plus")
      -> Same pipeline, gives CPM/cast count benchmarks for full run

   c. get_defensive_patterns(spec, encounter_id, difficulty="mythic_plus")
      -> Death timing + defensive usage patterns across full run

3. Cache all results (6h TTL, same as raid)
```

### Key Insight: WCL M+ Rankings Return Full-Run Data

When you query `characterRankings` for a dungeon encounter with `difficulty: 10`, WCL returns rankings for the **entire dungeon run**. Each ranking entry gives you a `report.code` and `report.fightID` -- that `fightID` is the aggregate fight for the whole dungeon.

This means the existing event-fetching code (cast events, damage events, buff tables) works on the full dungeon. **No per-segment benchmark fetching is needed at the benchmark level.** Per-segment analysis happens at the comparison stage.

### Required Changes to Support This

1. **`builds.py`**: Add `"mythic_plus": 10` to `DIFFICULTY_MAP`
2. **All benchmark tools**: Already parameterized on `difficulty` -- they will just work
3. **`_mplus_benchmarks.py` (NEW)**: Thin orchestrator that calls existing benchmark tools with `difficulty="mythic_plus"` and bundles results into an `MplusBenchmarkBundle` model

```python
# _mplus_benchmarks.py -- conceptual structure
async def fetch_mplus_benchmarks(
    client: WCLClient,
    spec: str,
    encounter_id: int,  # dungeon encounter ID
) -> MplusBenchmarkBundle:
    """Parallel-fetch all M+ benchmarks for a dungeon."""
    rotation, timelines, defensives = await asyncio.gather(
        get_rotation_profile(client, spec, encounter_id, "mythic_plus"),
        get_cooldown_timelines(client, spec=spec, encounter_id=encounter_id,
                               difficulty="mythic_plus", sample_size=20),
        get_defensive_patterns(client, spec=spec, encounter_id=encounter_id,
                                difficulty="mythic_plus"),
        return_exceptions=True,
    )
    return MplusBenchmarkBundle(rotation=rotation, timelines=timelines, defensives=defensives)
```

### Rate Limit Budget

M+ benchmarks cost the same as raid benchmarks per tool call. For a coaching session:
- `get_rotation_profile`: ~80 points (5 players)
- `get_cooldown_timelines`: ~60-150 points (20 players at reduced sample)
- `get_defensive_patterns`: ~60 points (10 players)
- Total: ~200-290 points first call, **0 points** on subsequent calls (cached 6h)

Use `sample_size=20` (not 50) for M+ timelines to conserve budget. M+ data has more variance than raid data anyway, so diminishing returns beyond 20 samples.

## Question 3: Per-Segment Analysis Strategy

**Decision: Build new `_mplus_comparisons.py` that CALLS INTO existing comparison functions, but adds M+ segment-aware orchestration.**

### Why Not Directly Reuse `analyze_player_log`

`analyze_player_log` is designed for a single boss fight: one encounter_id, one fight_id, one set of benchmarks. M+ coaching needs:

1. **Full-run comparison**: Player's entire dungeon vs. dungeon-wide benchmarks (this maps well to existing comparisons)
2. **Per-segment breakdown**: Which trash packs / bosses were weak (this is new)
3. **Death correlation**: Deaths correlated with incoming damage spikes (new)
4. **CD spacing analysis**: Were CDs held too long between segments? (new)

### Reuse Matrix

| Comparison Function | Reusable for M+ Full-Run? | Reusable for Per-Segment? |
|---------------------|---------------------------|---------------------------|
| `compare_rotation` | YES -- full-run spell counts vs benchmark | Partial -- need segment spell counts |
| `compare_cooldowns` | YES -- full-run CD usage vs benchmark | NO -- segment-level CD timing is different |
| `compare_defensives` | YES -- full-run defensive usage | Partial -- deaths per segment |
| `compare_build` | YES -- talents don't change per segment | YES (identical) |
| `compare_talent_usage` | YES | NO -- not meaningful per segment |
| `compare_cd_throughput` | YES -- full-run CD windows | Not needed per segment |
| `analyze_deaths` | YES | YES -- with segment context |
| `analyze_downtime` | Partial -- M+ has inter-pull downtime that's expected | NO -- different meaning in M+ |

### Recommended Structure for `_mplus_comparisons.py`

```python
def compare_mplus_full_run(
    player_data: DungeonRunAnalysisResponse,
    benchmarks: MplusBenchmarkBundle,
    spec: str,
) -> MplusFullRunComparison:
    """Full-run gap analysis: player's dungeon vs M+ benchmarks."""
    # Reuse existing comparison functions
    rotation_gaps = compare_rotation(spell_counts, spell_names, duration, benchmarks.rotation)
    cooldown_issues = compare_cooldowns(spell_counts, spell_names, benchmarks.timelines)
    defensive_issues = compare_defensives(spell_counts, spell_names, benchmarks.defensives)
    build_divergence = compare_build(talents, benchmarks.builds, spec=spec)
    ...

def analyze_segment_performance(
    segments: list[FightSegmentSummary],
    segment_details: list[SegmentDetailData],
    benchmarks: MplusBenchmarkBundle,
) -> list[SegmentAnalysis]:
    """Per-segment analysis: identify weakest segments."""
    # NEW logic: compare each segment's DPS to full-run average
    # Identify segments where DPS drops significantly
    # Correlate deaths with specific segments
    # Flag segments with CD waste (CD available but not used)
    ...

def analyze_death_patterns(
    death_events: list[dict],
    segments: list[FightSegmentSummary],
    damage_taken_events: list[dict],
) -> MplusDeathAnalysis:
    """NEW: Death correlation with incoming damage."""
    # Group deaths by segment
    # Identify killing blow abilities
    # Check if defensive CDs were available but unused
    ...
```

## Question 4: Tool Structure

**Decision: Three focused tools, not one monolithic tool.**

### Tool Design

| Tool | Purpose | Cost | When to Use |
|------|---------|------|-------------|
| `analyze_dungeon_run` (existing) | Quick overview: DPS, deaths, segments | ~5-7 pts | First look at a run |
| `get_mplus_benchmarks` (NEW) | Fetch/cache M+ benchmarks for a dungeon | ~200 pts first, 0 cached | Before coaching |
| `coach_dungeon_run` (NEW) | Full coaching: gap analysis + segment breakdown + actionable advice | ~50-100 pts + benchmark cost | Deep coaching session |

### Why Three Tools, Not One

1. **Separation of concerns**: `analyze_dungeon_run` is a data tool (what happened). `coach_dungeon_run` is an analysis tool (what to improve). Different purposes, different costs.

2. **Claude can decide**: With separate tools, Claude can do a quick `analyze_dungeon_run` first, then decide if full coaching is warranted. One monolithic tool forces the expensive path every time.

3. **Cache warming**: `get_mplus_benchmarks` can be called once per dungeon per session. Subsequent `coach_dungeon_run` calls for different reports in the same dungeon reuse cached benchmarks.

4. **Rate limit budget**: A coaching session might analyze 3-4 runs in the same dungeon. With separated tools, benchmark cost is paid once (~200 pts), analysis cost is per-run (~50-100 pts). Monolithic tool would waste budget re-fetching benchmarks.

### Tool: `get_mplus_benchmarks`

```
get_mplus_benchmarks(spec, encounter_id)
  -> Returns: rotation profile, CD timelines, defensive patterns for top M+ players
  -> Cached 6 hours
  -> Cost: ~200 points first call, 0 after
```

### Tool: `coach_dungeon_run`

```
coach_dungeon_run(report, player, spec, fight="last")
  -> Step 1: Run analyze_dungeon_run internally (reuse existing)
  -> Step 2: Fetch benchmarks (get_mplus_benchmarks, cached)
  -> Step 3: Full-run comparison (reuse _analysis_comparisons)
  -> Step 4: Per-segment analysis (new _mplus_comparisons)
  -> Step 5: Death analysis (new)
  -> Step 6: Generate top_issues with actionable advice
  -> Returns: MplusCoachingResponse
  -> Cost: ~50-100 points (player data) + benchmark cost if not cached
```

## Question 5: Data Flow and Dependencies

### Dependency Graph

```
get_encounters ─────────────────────────┐
  (discover dungeon encounter IDs)      │
                                        v
get_mplus_benchmarks ─────────> MplusBenchmarkBundle (cached)
  |                                     |
  +-> get_rotation_profile(difficulty="mythic_plus")
  +-> get_cooldown_timelines(difficulty="mythic_plus")
  +-> get_defensive_patterns(difficulty="mythic_plus")
                                        |
                                        v
coach_dungeon_run ──────────────────────+
  |                                     |
  +-> analyze_dungeon_run (reuse)       | (benchmarks from cache)
  |     +-> _query_all_fights           |
  |     +-> _query_master_data          |
  |     +-> damage/buff/death queries   |
  |                                     |
  +-> _mplus_comparisons ──────────────>+
        +-> compare_rotation (reuse)
        +-> compare_cooldowns (reuse)
        +-> compare_defensives (reuse)
        +-> compare_build (reuse)
        +-> analyze_segment_performance (NEW)
        +-> analyze_death_patterns (NEW)
```

### Parallelization Opportunities

| Step | Can Parallelize? | Details |
|------|------------------|---------|
| Benchmark fetch (3 tools) | YES | `asyncio.gather(rotation, timelines, defensives)` -- already done by existing tools |
| Player data collection | YES | `asyncio.gather(fights, masterData)` -- already done by dungeon_analysis |
| Per-segment DPS queries | YES | `asyncio.gather(*[damage_query(f) for f in fights])` -- already done |
| Benchmark fetch + Player data | YES | These are independent -- can run in parallel |
| Comparison analysis | NO (CPU-bound) | Runs after both benchmark + player data are ready, but is fast (~ms) |

### Optimal Data Flow for `coach_dungeon_run`

```python
async def coach_dungeon_run(client, report, player, spec, fight="last"):
    # Phase 1: Parallel -- fetch player data AND benchmarks simultaneously
    player_result, benchmarks = await asyncio.gather(
        analyze_dungeon_run(client, report, player, spec, fight, include_casts=True),
        fetch_mplus_benchmarks(client, spec, encounter_id_from_dungeon),
    )

    # Problem: encounter_id is only known after analyzing the dungeon run
    # Solution: Two-phase approach

    # Phase 1a: Quick dungeon identification (reuse _query_all_fights)
    fights, _ = await _query_all_fights(client, report_code)
    selected_run = _select_dungeon_run(_group_fights_by_dungeon(fights), fight)
    encounter_id = _resolve_dungeon_encounter_id(selected_run)

    # Phase 1b: NOW parallel -- we know encounter_id
    player_result, benchmarks = await asyncio.gather(
        _analyze_dungeon_full(client, report_code, player, spec, selected_run),
        fetch_mplus_benchmarks(client, spec, encounter_id),
    )

    # Phase 2: Comparison (CPU-only, fast)
    coaching = compare_mplus_full_run(player_result, benchmarks, spec)
    segment_analysis = analyze_segment_performance(player_result.segments, ...)
    death_analysis = analyze_death_patterns(...)

    return MplusCoachingResponse(...)
```

### Encounter ID Resolution for M+ Dungeons

**Critical implementation detail:** The existing `analyze_dungeon_run` identifies dungeons by `gameZone.id` (zone ID), but benchmark tools need an `encounter_id` (the WCL encounter ID for the dungeon). These are different values.

Resolution approach:
1. Use `get_encounters(content_type="mythic_plus")` to get the zone->encounter mapping
2. Cache this mapping (24h TTL, same as encounters)
3. In `coach_dungeon_run`, map `zone_id -> encounter_id` via this cached mapping

Alternatively, the zone's encounter list from `get_encounters` gives us the encounter ID directly. Each M+ dungeon zone typically has exactly one encounter entry.

## Patterns to Follow

### Pattern 1: Difficulty Abstraction

Extend `DIFFICULTY_MAP` and let all benchmark tools work for M+ without code changes:

```python
DIFFICULTY_MAP: dict[str, int] = {
    "normal": 3,
    "heroic": 4,
    "mythic": 5,
    "mythic_plus": 10,  # NEW
}
```

### Pattern 2: Benchmark Bundle

Group related benchmarks into a single fetch-and-cache unit:

```python
class MplusBenchmarkBundle(BaseModel):
    spec: str
    encounter_id: int
    dungeon_name: str
    rotation: Optional[RotationProfileResponse] = None
    timelines: Optional[CooldownTimelineResponse] = None
    defensives: Optional[DefensivePatternResponse] = None
```

### Pattern 3: Two-Phase Orchestration

Separate "identify what to analyze" from "analyze it":

```
Phase 1: Identify (cheap: 1-2 API points)
  -> Which dungeon? Which segments? What encounter_id?

Phase 2: Fetch + Analyze (expensive: parallelized)
  -> Player data AND benchmarks in parallel
  -> Comparison is CPU-only
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Per-Segment Benchmark Fetching

**What:** Fetching separate benchmarks for each trash pack or boss within a dungeon.
**Why bad:** M+ rankings are for full dungeons, not individual segments. WCL does not provide per-segment rankings. Would waste API points on queries that return no useful data.
**Instead:** Use full-dungeon benchmarks and derive per-segment expectations by proportion.

### Anti-Pattern 2: Duplicating Comparison Logic

**What:** Writing new rotation/CD/defensive comparison code in `_mplus_comparisons.py` that duplicates `_analysis_comparisons.py`.
**Why bad:** Two copies to maintain, divergent behavior over time.
**Instead:** Import and call existing comparison functions. Only write NEW logic for M+-specific concerns (segment analysis, CD spacing between pulls, death correlation).

### Anti-Pattern 3: Monolithic Tool

**What:** One `analyze_and_coach_mplus_run` tool that does everything.
**Why bad:** Forces expensive benchmark fetch on every call. No way for Claude to do a quick look first. Harder to test. Violates existing pattern of focused tools.
**Instead:** Separate data tool (analyze) from coaching tool (coach), with explicit benchmark caching.

### Anti-Pattern 4: Separate M+ Cache Directory

**What:** Creating `~/.cache/wow-mcp/mplus/` or similar separate cache structure.
**Why bad:** The existing key-based cache already namespaces via the key string. Adding directory structure adds complexity for zero isolation benefit.
**Instead:** Use descriptive cache keys like `mplus_benchmarks_{spec}_{encounter_id}`.

## Suggested Build Order

Based on dependency analysis, build in this order:

### Phase 1: Foundation (enables all M+ benchmark tools)
1. Add `"mythic_plus": 10` to `DIFFICULTY_MAP` in `builds.py`
2. Add new Pydantic models to `models.py` (`MplusBenchmarkBundle`, `MplusCoachingResponse`, `SegmentAnalysis`, etc.)
3. Verify existing benchmark tools work with `difficulty="mythic_plus"` (test with WCL API)

### Phase 2: Benchmark Orchestrator
4. Create `_mplus_benchmarks.py` -- thin wrapper calling existing tools
5. Create `get_mplus_benchmarks` tool in `server.py`
6. Test: fetch M+ benchmarks for a dungeon, verify cache behavior

### Phase 3: Comparison Engine
7. Create `_mplus_comparisons.py` -- full-run comparisons (reusing existing) + segment analysis (new)
8. Implement death correlation analysis
9. Implement CD spacing / waste detection across segments

### Phase 4: Coaching Tool
10. Create `mplus_coach.py` with `coach_dungeon_run` orchestrator
11. Register in `server.py`
12. Optional: enhance `dungeon_analysis.py` with richer segment data for coaching consumption

### Phase 5: Polish
13. Update `coaching.py` (`get_coaching_context`) with M+ workflow guidance
14. Integration testing with real M+ logs

## Sources

- Existing codebase analysis (HIGH confidence)
- [WCL API v2 Encounter docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/encounter.doc.html) (MEDIUM confidence -- 403 on direct access, inferred from existing code + forum discussions)
- [WCL API v2 Query docs](https://www.warcraftlogs.com/v2-api-docs/warcraft/query.doc.html)
- [WCL Forum: M+ Rankings Discussion](https://forums.combatlogforums.com/t/mythic-dungeons-rankings-discussion/662) (MEDIUM confidence)
- [WCL M+ Rankings page](https://www.warcraftlogs.com/zone/rankings/34) (confirms M+ dungeons are ranked as encounters)
- [Keystone Heroes project](https://github.com/ljosberinn/keystone-heroes) (archived, but validates WCL M+ data model approach)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Cache strategy | HIGH | Directly follows from reading cache.py -- key-based, no structural changes needed |
| Benchmark pipeline reuse | HIGH | Existing tools are parameterized on difficulty -- adding mythic_plus=10 is mechanical |
| Per-segment comparison design | HIGH | Clearly follows from reading _analysis_comparisons.py -- functions are stateless, composable |
| Tool structure (3 tools) | HIGH | Follows existing patterns (analyze vs coaching separation) |
| WCL M+ difficulty=10 | MEDIUM | Inferred from WCL forum + pattern analysis; needs API verification in Phase 1 |
| WCL M+ rankings data shape | MEDIUM | Assumed same as raid rankings with report.code + report.fightID; needs verification |
| Encounter ID resolution | MEDIUM | Zone->encounter mapping assumed from get_encounters output; needs verification |
