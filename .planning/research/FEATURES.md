# Feature Landscape: M+ Coaching Intelligence

**Domain:** Mythic+ dungeon coaching for WoW (DPS focus)
**Researched:** 2026-03-28
**Confidence:** MEDIUM (WCL API M+ ranking structure needs verification during implementation)

## Context: What Exists vs What's New

The existing `analyze_dungeon_run` tool (MPLUS-01) provides a solid single-run analysis foundation: aggregate DPS, per-segment breakdown, damage/deaths, buff uptimes, talent snapshot, and optional cast data. The M+ coaching milestone builds **benchmark comparison** and **structured coaching** on top of this — the same pattern that raid coaching followed (Phases 2-7), adapted for M+ specifics.

Key difference from raid coaching: M+ has no single "encounter" to benchmark against. Performance is route-dependent, pull-size-dependent, key-level-dependent, and affix-dependent. Benchmarks must account for this variability.

---

## Table Stakes

Features users expect from any M+ coaching tool. Missing = product feels incomplete compared to what Keystone Heroes (archived), WoWAnalyzer, and class-specific guides (e.g., Peak of Serenity) provide.

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| **M+ benchmark aggregation** | Players need a "what good looks like" reference. WCL characterRankings exists for M+ encounters. Without benchmarks, coaching is just descriptive, not comparative. | Medium | `get_encounters` (dungeon zone IDs), WCL `characterRankings` with M+ difficulty bracket | WCL ranks M+ by key level brackets. Must query `difficulty: 10` (or appropriate M+ difficulty constant). Verify exact GraphQL field — raid uses `difficulty: 5` for Mythic, M+ uses different values. |
| **M+ cooldown timeline (full-run)** | CD spacing across an entire dungeon run is THE core M+ coaching signal. Top players align major CDs to specific pulls/bosses. Wasted CDs or CDs held too long = massive DPS loss. | Medium-High | Existing `get_cooldown_timelines` pattern, benchmark rankings data | Unlike raid (single fight), M+ CDs span 30+ minutes across 20-40 pulls. Must show CD usage relative to pull boundaries, not just raw timestamps. Segment-aware timeline is critical. |
| **M+ rotation profile (per-dungeon benchmarks)** | CPM, buff uptimes, and cast priorities differ between M+ and raid. AoE-heavy dungeons shift spell priority. Players need dungeon-specific baselines. | Medium | Existing `get_rotation_profile` pattern, benchmark rankings data | Top M+ players may have very different spell distributions vs raid. Key insight: M+ rotation includes trash AoE + boss ST transitions. |
| **M+ per-segment gap analysis** | Players need to see WHERE they lost damage — which trash pack, which boss, which transition. "Your DPS dropped 40% on segments 5-8 vs top players" is actionable. | High | Benchmark data + per-segment player data from `analyze_dungeon_run` | Most complex feature. Needs per-segment benchmark reference AND per-segment player data. Segment naming/matching across different routes is the hard problem. |
| **M+ death analysis** | 90% of M+ failures come from deaths. Deaths = depleted keys. Correlating deaths with incoming damage events and missing defensives is essential. | Medium | Death events from `analyze_dungeon_run`, defensive spell data | Already partially exists in dungeon_analysis.py (death_times, segment_deaths). Needs: damage taken breakdown before death, defensive CD availability at time of death, avoidable vs unavoidable classification. |
| **M+ defensive patterns** | When do top players use defensives? In M+, defensive timing is pull-dependent (big pulls need personals). Pattern differs from raid (boss mechanics). | Medium | Existing `get_defensive_patterns` pattern, benchmark rankings data | Defensive usage in M+ is more reactive and pull-size-dependent than raid. Benchmarks less stable, but still valuable for "top players use X defensive Y times per dungeon." |

---

## Differentiators

Features that set this MCP coaching tool apart. Not expected, but highly valuable when present. These leverage the unique advantage of Claude as an AI coach interpreting structured data.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|-------------------|------------|--------------|-------|
| **Cooldown-to-pull mapping** | Map each major CD usage to the specific pull it was used on, with pull mob count/difficulty context. "You used Combustion on a 3-mob trash pack at 4:30 — top players save it for the 8-mob pull at 5:15." | Medium | Segment fight data + CD timeline | This is what Keystone Heroes did before it was archived. Combining CD timestamps with segment boundaries creates the most actionable coaching insight. |
| **CD waste detection** | Detect CDs that came off cooldown but were not used for extended periods. "Your Incarnation was available for 45s before you used it — that's a full extra use over the dungeon." | Low | CD timeline data, spell cooldown durations from spec data | Simple math: time between CD uses vs spell cooldown. If gap >> cooldown, that's waste. High coaching value, low implementation cost. |
| **Interrupt analysis** | Track interrupt casts per dungeon, compare vs team interrupt count, identify missed critical kicks. M+ groups fail when kicks are uncoordinated. | Medium | Cast event data (interrupt spells), enemy cast data | Requires querying interrupt events. High value for M+ (unlike raid where interrupt is less central). Could compare player interrupt count vs top-player baselines. |
| **Pull-by-pull DPS curve** | Visualize DPS across the entire dungeon as a timeline, with pull boundaries marked. Shows when player "falls off" vs maintains consistent output. | Low-Medium | Per-segment DPS (already in dungeon_analysis), segment ordering | Already have per-segment DPS. Structuring it as a sequence with segment names creates a "DPS curve" Claude can interpret and coach on. |
| **Key level scaling context** | Normalize player performance against key level. A 500k DPS in a +7 is different from 500k in a +12. Provide "expected DPS at this key level" context. | Low | WCL ranking brackets, key level from report | WCL brackets M+ by key level. Providing scaling context ("at +10, top Frost Mages do X; at +15, they do Y") helps players understand their trajectory. |
| **Affix-aware coaching** | Adjust coaching advice based on current affixes (Fortified vs Tyrannical, seasonal affixes). Fortified weeks = trash damage matters more; Tyrannical = boss damage matters more. | Low | Affix data from WCL report or Blizzard API | Low complexity because it's primarily prompt context, not data processing. Claude already knows affix mechanics — just needs the affix info passed through. |

---

## Anti-Features

Features to explicitly NOT build in this milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Route optimization / MDT integration** | Routes are team decisions, not individual coaching. Route data requires MDT addon export, not WCL API. Completely different data pipeline. | Focus on "given your route, here's how to improve execution." Acknowledge route choice in coaching context. |
| **Healer/tank-specific coaching** | Different metric model entirely (HPS, damage taken, threat, kiting patterns). DPS coaching is already complex enough for v2.0. | Keep DPS focus. Out of Scope in PROJECT.md already. |
| **Real-time or live coaching** | MCP is post-game analysis. Real-time requires addon integration, latency considerations, fundamentally different architecture. | Post-game analysis with "next time, do X" framing. |
| **Cross-dungeon meta analysis** | "Your best dungeon is X, worst is Y" requires analyzing multiple reports. Useful but scope creep for v2.0. | Single-dungeon-run coaching first. Meta-analysis is a future milestone. |
| **Detailed trash mob identification** | Identifying specific trash pack composition from WCL segment data is unreliable. Segments are time-based, not pack-based. | Use segment names/indices and boss names. "Segment 3 (between Boss 1 and Boss 2)" rather than "the Spellbound Sentry pack." |
| **Group composition analysis** | Analyzing all 5 players' synergies requires 5x the API budget and different coaching model. | Single-player focus. "Your interrupt count was X" not "your group's interrupt coordination was Y." |

---

## Feature Dependencies

```
get_encounters (existing) ──> M+ benchmark aggregation
                                  │
                                  ├──> M+ rotation profile
                                  ├──> M+ cooldown timeline
                                  ├──> M+ defensive patterns
                                  │
                                  └──> [all feed into]
                                            │
analyze_dungeon_run (existing) ──> M+ per-segment gap analysis
                                            │
                                  ├──> M+ death analysis (enhanced)
                                  ├──> CD waste detection
                                  ├──> Cooldown-to-pull mapping
                                  └──> Pull-by-pull DPS curve
```

**Critical path:** M+ benchmark aggregation MUST come first. Every comparison feature depends on having benchmark data to compare against. This mirrors raid coaching where `get_top_builds` / `get_rotation_profile` / `get_cooldown_timelines` had to exist before `analyze_player_log` could do gap analysis.

**Parallel work possible:** Once benchmarks exist, cooldown timeline, rotation profile, and defensive patterns can be built in parallel (same pattern as raid phases 3-4).

---

## MVP Recommendation

### Phase 1: Benchmarks (foundation)
1. **M+ benchmark aggregation** — Query WCL characterRankings for M+ dungeon encounters, extract top-player reports for downstream analysis. This is the data foundation everything else needs.

### Phase 2: Core coaching signals (highest coaching value)
2. **M+ cooldown timeline** — Full-run CD spacing from top players. Combined with the existing `analyze_dungeon_run` cast data, enables "your CD timing vs theirs."
3. **M+ rotation profile** — Per-dungeon CPM/buff baseline from top players. Enables "your AoE spell usage is X% below benchmark."

### Phase 3: Defensive + death analysis
4. **M+ defensive patterns** — When top players press defensives across a dungeon run.
5. **M+ death analysis (enhanced)** — Damage-taken-before-death breakdown, defensive availability check, avoidable damage flagging.

### Phase 4: Structured coaching
6. **M+ per-segment gap analysis** — The crown jewel: segment-by-segment comparison with structured actionable output. Requires all benchmark data from phases 1-3.

### Defer to future milestone:
- **Interrupt analysis** — High value but requires separate event type queries. Good candidate for v2.1.
- **Cross-dungeon meta** — Requires multi-report orchestration. v3.0 material.
- **Key level scaling context** — Nice-to-have, can be added incrementally to any tool.

---

## M+ vs Raid Coaching: Key Differences to Design For

| Dimension | Raid | M+ | Design Implication |
|-----------|------|-----|-------------------|
| Fight scope | Single encounter (2-10 min) | Full dungeon (25-40 min, 20-40 pulls) | M+ tools must handle multi-segment data. API budget much higher. |
| CD alignment | Align to boss phases/timers | Align to pull schedule/route | CD timeline needs segment boundaries, not boss phase markers |
| DPS consistency | Stable within fight | Highly variable (trash AoE vs boss ST) | Per-segment analysis critical; aggregate DPS less meaningful |
| Benchmark stability | Very stable (same fight every time) | Variable (route, key level, group comp, affixes) | Benchmarks are noisier. Need larger sample or bracket filtering. |
| Death meaning | Wipe → retry | Death → depleted key, no retry | Death analysis more critical in M+. Every death costs key time. |
| Defensives | Boss mechanic timing | Pull-size-dependent, reactive | Defensive pattern less predictable. Focus on "did you use them enough" over "exact timing." |
| Interrupt importance | Low (few interruptible boss casts) | Critical (trash casts must be kicked) | Interrupt tracking is table stakes for advanced M+ coaching. |

---

## WCL API Considerations for M+ Features

**Rankings query for M+:**
- Existing raid pattern: `worldData.encounter(id: X).characterRankings(difficulty: 5)` for Mythic raid
- M+ likely uses same structure but with dungeon encounter IDs and M+ difficulty constant
- **VERIFY:** What difficulty value represents M+ in WCL? Is it `difficulty: 10`? Does it accept `bracket` for key level filtering?
- **VERIFY:** Do M+ characterRankings return `report.code` + `report.fightID` like raid rankings? If so, existing report-querying patterns transfer directly.

**API budget:**
- Raid benchmark: ~5 reports x ~3 queries each = ~15 WCL points
- M+ benchmark: Same pattern but M+ reports are larger (more segments). Querying cast events for a 35-min dungeon = significantly more pagination.
- **Mitigation:** Cache aggressively. M+ benchmark data changes slowly (weekly with affixes, not per-pull).

**Segment matching challenge:**
- Player's segments may not match benchmark segments (different routes, different pull sizes)
- Cannot do 1:1 segment comparison in most cases
- **Approach:** Compare aggregate metrics (total CD uses, overall CPM) and boss-specific segments (bosses are consistent across routes). Trash-pull comparison is per-dungeon-average, not per-pull.

---

## Sources

- [WarcraftLogs Rankings](https://www.warcraftlogs.com/zone/rankings/39) — M+ Season 1 rankings structure (MEDIUM confidence)
- [WarcraftLogs API Docs](https://www.warcraftlogs.com/api/docs) — GraphQL API reference (HIGH confidence)
- [WarcraftLogs Ranking Help](https://www.warcraftlogs.com/help/ranks/) — Bracket and difficulty system (MEDIUM confidence)
- [Keystone Heroes (archived)](https://github.com/ljosberinn/keystone-heroes) — Feature reference for M+ analysis: routes, CD usage, improvement vectors (HIGH confidence on feature set)
- [MythicStats](https://mythicstats.com/) — M+ meta statistics and DPS rankings (MEDIUM confidence)
- [Peak of Serenity M+ Log Analysis](https://www.peakofserenity.com/tww/windwalker/pve-guide/log-analysis/mythicplus/) — Class-specific M+ log review methodology (MEDIUM confidence)
- [Tanknotes M+ Tips](https://tanknotes.com/mythic-plus/dungeon-tips/) — Pull planning, CD mapping, defensive rotation patterns (MEDIUM confidence)
- [Wipefest](https://www.wipefest.gg/) — Raid analysis patterns (avoidable damage, interrupt tracking) that inform M+ feature design (HIGH confidence on patterns)
- Existing codebase: `dungeon_analysis.py`, `rotation.py`, `timelines.py`, `_analysis_comparisons.py` — Pattern reference for implementation (HIGH confidence)
