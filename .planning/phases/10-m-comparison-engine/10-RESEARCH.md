# Phase 10: M+ Comparison Engine - Research

**Researched:** 2026-03-28
**Domain:** M+ dungeon performance comparison (player vs benchmark)
**Confidence:** HIGH

## Summary

Phase 10 builds a comparison engine that takes a player's M+ dungeon run and compares it against Phase 9 benchmarks. The phase requires one new MCP tool (`compare_mplus_run`) and supporting Pydantic models. The codebase has strong precedents: `_analysis_comparisons.py` provides raid-style comparison patterns (rotation, cooldowns, defensives), `mplus_benchmarks.py` provides segment extraction functions, and `dungeon_analysis.py` provides player data extraction. The primary challenge is orchestrating these into a unified comparison pipeline that aligns player segments to benchmark segments by position and handles both trash (aggregate) and boss (cast-by-cast) analysis modes.

Death analysis with damage-taken breakdown is the only genuinely new WCL query type needed -- the codebase has `_query_death_events` (Deaths dataType) but no DamageTaken events query for the time window around each death. Interrupt comparison requires matching player interrupt targets against benchmark targets, which extends beyond simple count comparison.

**Primary recommendation:** Reuse existing extraction functions from `mplus_benchmarks.py` for player segment data, reuse `_analysis_comparisons.py` patterns for boss comparison, and add new DamageTaken + defensive availability queries for death analysis.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Reuse `analyze_dungeon_run` output for player's per-segment data; extend with CD/interrupt extraction using `_extract_segment_cds`, `_count_segment_interrupts`
- Single `compare_mplus_run` function returns all comparisons in one call
- Gap identification threshold: flag if player is >20% below benchmark median
- Incomplete segments marked as "incomplete" with available data, not skipped
- Reuse `_analysis_comparisons.py` patterns for M+ boss fights
- Per-death breakdown: damage-taken sources + "was defensive available?" check
- Per-segment interrupt count + critical missed kick identification
- Critical missed interrupts: compare player's interrupt targets vs benchmark interrupt targets
- Parameters: `report_code, player_name, encounter_id, spec, key_level, fight="last"`
- Response: hierarchical structure -- segment_comparisons[] + boss_comparisons[] + death_analysis + summary
- Include benchmark values inline in each comparison (player_value, benchmark_value, gap_pct)

### Claude's Discretion
- Internal module structure (single file vs split)
- Pydantic model field names (follow existing conventions)
- Exact logic for "was defensive available?" check
- How to handle boss fights where player wipes (incomplete boss data)

### Deferred Ideas (OUT OF SCOPE)
None.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DMG-02 | Compare player's spell damage % per trash segment against benchmark | Reuse `_extract_segment_damage` for player data, align by position to `MplusBenchmarkSegment.damage_breakdown`, compute gap_pct |
| BOSS-01 | Run raid-style cast-by-cast analysis on each boss within M+ dungeon | Reuse `_extract_boss_benchmark` pattern for player boss data, compare cast counts/CPM per spell against benchmark boss segment |
| BOSS-02 | Compare player's boss performance against benchmarks (rotation, CDs, defensives) | Adapt `compare_rotation`/`compare_cooldowns`/`compare_defensives` from `_analysis_comparisons.py` for M+ boss context |
| SURV-02 | Analyze player deaths with damage-taken breakdown and defensive availability | New WCL query: DamageTaken events in window around death timestamp; cross-reference defensive CDs from `_build_tracked_spells` |
| INT-02 | Compare player's interrupt usage against benchmark (count, critical kicks missed) | Reuse `_count_segment_interrupts` + new interrupt target extraction; diff player targets vs benchmark targets |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | >=2.0 | Comparison result models | Project convention -- all tool responses are Pydantic models |
| httpx | latest | WCL API client (already in use) | Existing async HTTP client |
| mcp | >=1.25 | Tool registration | Project MCP framework |

### Supporting
No new dependencies. All required functionality exists in the codebase.

## Architecture Patterns

### Recommended Module Structure
```
src/tools/
  mplus_comparison.py     # New: compare_mplus_run orchestrator + comparison logic
src/
  models.py               # Extended: new comparison response models
  server.py               # Extended: register compare_mplus_run tool
```

Single file (`mplus_comparison.py`) is sufficient. The comparison logic follows a clear pipeline and won't exceed 800 lines given heavy reuse of existing functions. If it approaches the limit, split into `_mplus_comparison_helpers.py`.

### Pattern 1: Comparison Pipeline
**What:** Linear pipeline: fetch player data -> fetch benchmark -> align segments -> compare per-segment -> aggregate results
**When to use:** This is the core pattern for `compare_mplus_run`

```python
async def compare_mplus_run(client, report_code, player_name, encounter_id, spec, key_level, fight="last"):
    # Step 1: Get player's dungeon data (reuse existing functions)
    fights, title = await _query_all_fights(client, report_code)
    runs = _group_fights_by_dungeon(fights)
    selected_run = _select_dungeon_run(runs, fight)

    # Step 2: Get benchmark data (cached)
    benchmark = await get_mplus_benchmarks(client, spec, encounter_id, key_level)

    # Step 3: Build player segments aligned to benchmark
    player_segments = _build_segment_positions(seg_fights, boss_names)

    # Step 4: Per-segment comparison (parallel)
    segment_comparisons = []
    boss_comparisons = []
    for player_seg, bench_seg in _align_segments(player_segments, benchmark.segments):
        if player_seg["segment_type"] == "trash":
            segment_comparisons.append(_compare_trash_segment(player_seg, bench_seg))
        else:
            boss_comparisons.append(await _compare_boss_segment(client, ...))

    # Step 5: Death analysis
    death_analysis = await _analyze_deaths_with_breakdown(client, ...)

    # Step 6: Summary
    return MplusComparisonResponse(...)
```

### Pattern 2: Segment Alignment by Position
**What:** Match player segments to benchmark segments by position index
**When to use:** Both player and benchmark use `_build_segment_positions` with the same boss names, producing matching position indices

```python
def _align_segments(player_segs, bench_segs):
    """Align player segments to benchmark by position."""
    bench_by_pos = {s.position: s for s in bench_segs}
    for p_seg in player_segs:
        b_seg = bench_by_pos.get(p_seg["position"])
        yield p_seg, b_seg  # b_seg may be None if positions don't match
```

### Pattern 3: Gap Flagging with 20% Threshold
**What:** Flag metrics where player is >20% below benchmark median
**When to use:** All numeric comparisons (damage %, CD counts, interrupt counts)

```python
def _compute_gap(player_value, benchmark_value):
    if benchmark_value <= 0:
        return {"gap_pct": 0.0, "flagged": False}
    gap_pct = round((benchmark_value - player_value) / benchmark_value * 100, 1)
    return {"gap_pct": gap_pct, "flagged": gap_pct > 20.0}
```

### Pattern 4: Death Breakdown with Defensive Check
**What:** For each death, query DamageTaken events in the 10-15s window before death, then check if defensive CDs were available
**When to use:** SURV-02 requirement

```python
# 1. Get death events (existing _query_death_events)
# 2. For each death, query DamageTaken events in [death_ts - 15s, death_ts]
# 3. Get player's defensive CD spell IDs from _build_tracked_spells
# 4. Check cast events: was each defensive used before death?
#    - If defensive was cast recently (within its cooldown), it was "on cooldown"
#    - If defensive was NOT cast AND NOT on cooldown, flag as "available but unused"
```

### Anti-Patterns to Avoid
- **Don't re-query data that `get_mplus_benchmarks` already cached:** Always call `get_mplus_benchmarks` first (6h cache), don't build parallel benchmark extraction.
- **Don't build custom segment alignment:** Reuse `_build_segment_positions` for both player and benchmark; positions are deterministic from boss names.
- **Don't run full `analyze_player_log` per boss:** The raid-style tool is heavyweight. Extract only the data needed (casts, CDs, defensives) using the lighter segment-level query functions.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Segment position alignment | Custom boss-matching logic | `_build_segment_positions` | Deterministic, already handles edge cases |
| Damage breakdown extraction | Custom WCL table parser | `_extract_segment_damage` + `_query_segment_damage_table` | Handles TopN, pct calculation |
| CD extraction per segment | Custom cast event filter | `_extract_segment_cds` + `_query_segment_cast_events` | Handles offensive/defensive split, tracked spell matching |
| Interrupt counting | Custom event counter | `_count_segment_interrupts` + `_query_segment_interrupt_events` | Paginated query, clean count |
| Boss cast comparison | New CPM calculator | Adapt `_extract_boss_benchmark` pattern | Already computes spell_counts + CPM |
| Benchmark data | Re-extract from reports | `get_mplus_benchmarks` (cached) | 6h cache, already aggregated |

**Key insight:** Phase 9 built all the extraction primitives. Phase 10's job is comparison logic on top of those primitives, not re-extraction.

## Common Pitfalls

### Pitfall 1: Segment Position Mismatch
**What goes wrong:** Player's run has different number of segments than benchmark (e.g., player wiped and reset, or pulled differently).
**Why it happens:** Different players may have different pull patterns, causing position misalignment.
**How to avoid:** Both player and benchmark use `_build_segment_positions` with the same boss names. Bosses are fixed per dungeon, so boss positions always align. Trash segments between same bosses get the same position. If a segment has no benchmark match, mark it "no_benchmark_data".
**Warning signs:** `len(player_segments) != len(benchmark_segments)` -- this is expected and normal.

### Pitfall 2: DamageTaken Query Returning Empty
**What goes wrong:** WCL `DamageTaken` events query returns empty for a death window.
**Why it happens:** WCL death events use `sourceID` for the player who died, but DamageTaken events might need `targetID` instead. The `sourceID` parameter in events query means "source of the action" for DamageDone but "target of the action" for DamageTaken.
**How to avoid:** Use `targetID` (not `sourceID`) when querying DamageTaken events. Alternatively, use `table(dataType: DamageTaken, targetID: X)` which is simpler.
**Warning signs:** Empty DamageTaken results despite player clearly dying.

### Pitfall 3: Defensive Availability False Positives
**What goes wrong:** Flagging a defensive as "available" when it was actually on cooldown from recent use.
**Why it happens:** Checking only "was it cast in this segment" misses the CD recovery window.
**How to avoid:** Query cast events for the full dungeon run (or at least the last N seconds before death) and check if the defensive was cast within its cooldown period before the death. Use `_build_tracked_spells(spec)` to get CD durations.
**Warning signs:** Player used a 3min defensive 2 minutes before death, but system flags it as "available".

### Pitfall 4: Interrupt Target Comparison Without Target Data
**What goes wrong:** Trying to compare interrupt targets when WCL interrupt events don't include what spell was interrupted.
**Why it happens:** WCL `dataType: Interrupts` returns source (interrupter) actions. The interrupted spell info is in the event's `ability` field (the interrupt ability used) and possibly related event data.
**How to avoid:** WCL Interrupts events DO include target information. The event has `abilityGameID` (the interrupt spell used), but the target's interrupted cast info may need correlation. Start with count comparison; target comparison may need `extraAbility` field in the interrupt event data.
**Warning signs:** Only getting the interrupter's spell, not what was interrupted.

### Pitfall 5: WCL API Budget Explosion
**What goes wrong:** Per-death DamageTaken queries * many deaths = massive API point consumption.
**Why it happens:** If player dies 8 times, that's 8 additional DamageTaken queries.
**How to avoid:** Batch death windows when possible (combine overlapping time ranges). Cap death analysis at ~5 deaths. Use table query instead of events query for DamageTaken (cheaper, returns aggregated data).
**Warning signs:** Total query cost exceeding 50 WCL points per comparison call.

### Pitfall 6: Boss Wipe Handling
**What goes wrong:** Player wipes on a boss (kill=false), comparison code crashes on missing data.
**Why it happens:** Wiped boss fights still appear in segment_fights but may have very short duration or incomplete data.
**How to avoid:** Check `kill` field or duration. For wipe fights, still extract what data exists but mark comparison as "incomplete". Use the decision from CONTEXT.md: "show what data exists + note the gap".
**Warning signs:** Boss fight duration < 30 seconds, `kill` is false.

## Code Examples

### Example 1: Trash Segment Damage Comparison
```python
def _compare_trash_damage(
    player_damage: list[SegmentDamageBreakdown],
    bench_damage: list[SegmentDamageBreakdown],
) -> list[dict]:
    """Compare player's spell damage % against benchmark per trash segment."""
    bench_by_id = {d.spell_id: d for d in bench_damage}
    comparisons = []
    for p in player_damage:
        b = bench_by_id.get(p.spell_id)
        if b:
            gap_pct = round(b.damage_pct - p.damage_pct, 1)
            comparisons.append({
                "spell_name": p.spell_name,
                "spell_id": p.spell_id,
                "player_pct": p.damage_pct,
                "benchmark_pct": b.damage_pct,
                "gap_pct": gap_pct,
                "flagged": gap_pct > 20.0,
            })
        else:
            comparisons.append({
                "spell_name": p.spell_name,
                "spell_id": p.spell_id,
                "player_pct": p.damage_pct,
                "benchmark_pct": 0.0,
                "gap_pct": 0.0,
                "flagged": False,
            })
    return comparisons
```

### Example 2: DamageTaken Query for Death Breakdown
```python
async def _query_damage_taken_events(
    client: WCLClient, report_code: str,
    start_time: int, end_time: int, target_id: int,
) -> list[dict]:
    """Query DamageTaken events targeting the player in a time window."""
    all_events = []
    next_ts = start_time
    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(startTime: {next_ts}, endTime: {end_time},
                           targetID: {target_id}, dataType: DamageTaken,
                           limit: 10000)
                    {{ data nextPageTimestamp }}
                }}
            }}
        """
        data = await client.query(gql)
        block = data.get("reportData", {}).get("report", {}).get("events", {})
        all_events.extend(block.get("data", []))
        next_ts = block.get("nextPageTimestamp")
    return all_events
```

### Example 3: Defensive Availability Check
```python
def _check_defensive_availability(
    death_ts: int, cast_events: list[dict],
    tracked_spells: dict[int, dict], segment_start: int,
) -> list[dict]:
    """Check which defensives were available when player died."""
    results = []
    for spell_id, info in tracked_spells.items():
        if info.get("ability_type") not in ("defensive", "raid_cd"):
            continue
        cd_ms = info.get("cd_seconds", 0) * 1000
        # 查找该技能最后一次使用时间
        last_cast_ts = None
        for ev in cast_events:
            if ev.get("abilityGameID") == spell_id and ev.get("timestamp", 0) <= death_ts:
                last_cast_ts = ev.get("timestamp")
        if last_cast_ts is None:
            status = "available_never_used"
        elif death_ts - last_cast_ts > cd_ms:
            status = "available_off_cooldown"
        else:
            status = "on_cooldown"
        results.append({
            "spell_name": info["name"],
            "spell_id": spell_id,
            "status": status,
            "last_cast_sec": round((last_cast_ts - segment_start) / 1000, 1) if last_cast_ts else None,
        })
    return results
```

### Example 4: Interrupt Target Comparison
```python
def _compare_interrupts(
    player_interrupt_events: list[dict],
    benchmark_interrupt_targets: set[int],  # spell_ids that benchmark players interrupt
) -> dict:
    """Compare player's interrupt targets against benchmark."""
    player_targets = {ev.get("abilityGameID", 0) for ev in player_interrupt_events}
    # Critical misses: spells that benchmark interrupts but player doesn't
    critical_missed = benchmark_interrupt_targets - player_targets
    return {
        "player_count": len(player_interrupt_events),
        "critical_missed_targets": list(critical_missed),
    }
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.24+ |
| Config file | `pyproject.toml` ([tool.pytest.ini_options]) |
| Quick run command | `uv run python -m pytest tests/test_mplus_comparison.py -x -q` |
| Full suite command | `uv run python -m pytest -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DMG-02 | Compare player damage % per trash segment vs benchmark | unit | `uv run python -m pytest tests/test_mplus_comparison.py::TestTrashDamageComparison -x` | No -- Wave 0 |
| BOSS-01 | Cast-by-cast analysis on M+ boss fights | unit | `uv run python -m pytest tests/test_mplus_comparison.py::TestBossComparison -x` | No -- Wave 0 |
| BOSS-02 | Compare player boss performance vs benchmarks | unit | `uv run python -m pytest tests/test_mplus_comparison.py::TestBossComparison -x` | No -- Wave 0 |
| SURV-02 | Death analysis with damage-taken + defensive check | unit | `uv run python -m pytest tests/test_mplus_comparison.py::TestDeathAnalysis -x` | No -- Wave 0 |
| INT-02 | Compare interrupt usage vs benchmark | unit | `uv run python -m pytest tests/test_mplus_comparison.py::TestInterruptComparison -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run python -m pytest tests/test_mplus_comparison.py -x -q`
- **Per wave merge:** `uv run python -m pytest -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_mplus_comparison.py` -- covers DMG-02, BOSS-01, BOSS-02, SURV-02, INT-02
- [ ] No new framework install needed -- pytest infrastructure is established

## Pydantic Models Needed

New models for `models.py` (following existing conventions):

```python
class SegmentDamageGap(BaseModel):
    """Single spell damage comparison within a segment."""
    spell_name: str
    spell_id: int = 0
    player_pct: float = 0.0
    benchmark_pct: float = 0.0
    gap_pct: float = 0.0
    flagged: bool = False

class SegmentComparison(BaseModel):
    """Comparison of one boss-bounded segment (trash or boss)."""
    position: int
    segment_type: str = ""  # "trash" or "boss"
    segment_name: str = ""
    status: str = ""  # "complete", "incomplete", "no_benchmark"
    damage_gaps: list[SegmentDamageGap] = Field(default_factory=list)
    cd_gaps: list[dict] = Field(default_factory=list)
    interrupt_comparison: dict = Field(default_factory=dict)

class BossCastComparison(BaseModel):
    """Cast-level boss comparison (raid-style)."""
    boss_name: str
    position: int
    player_duration_sec: float = 0.0
    benchmark_duration_sec: float = 0.0
    cast_gaps: list[dict] = Field(default_factory=list)  # per-spell cast count comparison
    cd_gaps: list[dict] = Field(default_factory=list)
    defensive_gaps: list[dict] = Field(default_factory=list)

class DeathBreakdown(BaseModel):
    """Single death with damage-taken sources and defensive availability."""
    death_time_sec: float
    segment_position: int = 0
    segment_name: str = ""
    damage_taken_sources: list[dict] = Field(default_factory=list)
    defensive_status: list[dict] = Field(default_factory=list)

class MplusComparisonResponse(BaseModel):
    """compare_mplus_run tool response."""
    report_code: str
    player_name: str
    spec: str
    dungeon_name: str = ""
    key_level: int = 0
    benchmark_key_level: int = 0
    segment_comparisons: list[SegmentComparison] = Field(default_factory=list)
    boss_comparisons: list[BossCastComparison] = Field(default_factory=list)
    death_analysis: list[DeathBreakdown] = Field(default_factory=list)
    interrupt_summary: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
```

## WCL API Queries Needed

| Query | Data Type | New? | Purpose | Est. Points |
|-------|-----------|------|---------|-------------|
| `_query_all_fights` | fights | No | Get player's fight list | 1 |
| `_query_master_data` | masterData | No | Get source_id | 1 |
| `_query_segment_damage_table` | DamageDone table | No | Per-segment damage breakdown | 1/segment |
| `_query_segment_cast_events` | Casts events | No | Per-segment CD/cast data | 1/segment |
| `_query_segment_interrupt_events` | Interrupts events | No | Per-segment interrupt data | 1/segment |
| `get_mplus_benchmarks` | (cached pipeline) | No | Benchmark data | 0 (cached) |
| `_query_damage_taken_events` | **DamageTaken events** | **YES** | Per-death damage breakdown | 1/death |
| `_query_death_events` | Deaths events | No | Death timestamps | 1 |

**Estimated total:** ~15-25 WCL points per comparison (10 segments * 3 queries + 1-5 death queries + 2 setup queries). Benchmark is free (cached).

## Open Questions

1. **DamageTaken event structure**
   - What we know: WCL supports `dataType: DamageTaken` for events queries. The `defensives.py` module references `DamageTaken` for table queries.
   - What's unclear: Exact event fields returned (spell name, source name, amount, absorbed, etc.) and whether `targetID` is the correct filter for "damage received by player".
   - Recommendation: Implement with `targetID` filter first. If events are empty, try without filter and post-filter by target name. Document the actual response shape in first integration test.

2. **Interrupt event target spell info**
   - What we know: WCL Interrupts data type exists and `_query_segment_interrupt_events` works for counting.
   - What's unclear: Whether interrupt events include the interrupted spell's ID/name (needed for "critical missed kicks" comparison).
   - Recommendation: Inspect raw interrupt event data in first implementation. Events likely have `targetAbility` or similar field. If not available, fall back to count-only comparison and flag as limitation.

## Sources

### Primary (HIGH confidence)
- `src/tools/mplus_benchmarks.py` -- all extraction functions verified by reading source
- `src/tools/_analysis_comparisons.py` -- comparison patterns verified by reading source
- `src/tools/dungeon_analysis.py` -- player data extraction verified by reading source
- `src/tools/analyze.py` -- death events query, cast events processing verified
- `src/models.py` -- all existing Pydantic models verified
- `src/tools/defensives.py` -- DamageTaken table reference confirmed

### Secondary (MEDIUM confidence)
- WCL DamageTaken events query structure -- inferred from existing DamageDone pattern and defensives.py reference
- Interrupt event target spell data -- needs runtime verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, pure reuse
- Architecture: HIGH -- follows established patterns with clear precedents
- Pitfalls: HIGH -- identified from codebase analysis and WCL API experience
- WCL DamageTaken queries: MEDIUM -- pattern inferred, not yet verified at runtime
- Interrupt target data: MEDIUM -- needs runtime verification of event fields

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable domain, no external dependency changes)
