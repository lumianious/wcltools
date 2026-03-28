# Domain Pitfalls: Adding M+ Coaching Intelligence

**Domain:** M+ coaching features added to existing WoW raid coaching MCP server
**Researched:** 2026-03-28

## Critical Pitfalls

Mistakes that cause rewrites, data corruption, or fundamentally wrong coaching advice.

### Pitfall 1: WCL Rankings API Structure Differs Between Raid and M+

**What goes wrong:** The existing codebase queries `worldData.encounter(id).characterRankings(difficulty: N)` with difficulty values 3/4/5 for normal/heroic/mythic raid. M+ dungeons use difficulty 10 and have a completely different data shape. Blindly reusing the raid ranking query pattern will either return empty results or wrong data.

**Why it happens:** The codebase has a `DIFFICULTY_MAP` (`builds.py:49`) that only maps `"normal": 3, "heroic": 4, "mythic": 5`. M+ difficulty 10 is absent. Every existing tool that queries rankings (builds, rotation, timelines, defensives, examples) uses this map. The `encounter(id)` for a dungeon refers to the dungeon itself (single encounter per zone), not individual bosses within it.

**Consequences:**
- `characterRankings(difficulty: 10)` returns M+ data, but the ranking entries have different fields: `bracketData` becomes keystone level (not item level), rankings are partitioned by keystone level and affix combo, and the `report` object points to a full dungeon run (not a single boss fight).
- The `report.fightID` from an M+ ranking points to the aggregate dungeon fight (encounterID > 0), not individual segment pulls. Fetching cast data for that single fightID gives you the entire run aggregated, losing per-segment granularity.
- `hasMorePages`/`page` pagination works the same, but the default page returns fewer meaningful results because M+ rankings are sparser per bracket.

**Prevention:**
- Add `"mythic_plus": 10` to `DIFFICULTY_MAP` and create a separate code path for M+ rankings.
- When consuming M+ ranking data, treat `bracketData` as keystone level, not item level.
- After fetching the report code from rankings, use the existing `_query_all_fights` + `_group_fights_by_dungeon` pattern (from `dungeon_analysis.py`) to get segment-level data, rather than relying on the single `fightID` from rankings.
- Verify: the encounter ID for a dungeon zone returns a single encounter (the dungeon itself). Test with the WCL GraphQL explorer before building.

**Detection:** Rankings returning 0 results, bracketData values in the 8-15 range (keystone levels) instead of 600+ range (item levels), or coaching advice comparing "item level 12" players.

**Phase relevance:** Must be addressed in the very first M+ benchmark phase.

---

### Pitfall 2: Rate Limit Exhaustion During Benchmark Building

**What goes wrong:** Building M+ benchmarks requires fetching rankings (1-2 points) then drilling into individual reports for cast/buff/damage data (5-30+ points each). Fetching 5 top players x 5-10 reports each can consume 150-600+ points in a single benchmark build. The system has 3600 points/hour total, shared across ALL tools.

**Why it happens:** The existing raid tools fetch rankings once per boss and drill into 3-5 reports. M+ benchmarks need more reports because:
1. Each dungeon has multiple segments (15-30 pulls) versus a single raid boss fight.
2. M+ data is noisier (different key levels, affixes, routes) so more samples are needed for reliable benchmarks.
3. Cast event pagination for a full M+ run is much longer than a single boss fight (the existing `dungeon_analysis.py` notes `include_casts=True` costs +30-100 points for full cast pagination).
4. Building benchmarks for 8 dungeons x 1 spec = 8x the queries versus a single boss.

**Consequences:** A single "build all dungeon benchmarks" call could consume the entire hourly budget (3600 points), blocking all other tools for up to an hour. The existing `_log_rate_limit` in `wcl_client.py` only logs; it does not throttle or reject calls.

**Prevention:**
- Implement a point budget system: each benchmark operation declares its expected cost upfront and checks `rate_limit.points_remaining` before proceeding.
- Build benchmarks lazily (per-dungeon, on-demand) not eagerly (all dungeons at once).
- Cache aggressively with appropriate TTLs (see Pitfall 5 for staleness concerns).
- Use the aggregate fight from rankings where possible (overall DPS, overall damage) instead of drilling into per-segment data for every benchmark player.
- Consider a "lite" benchmark mode that only fetches rankings + aggregate stats (2-3 points per dungeon) and a "deep" mode that drills into reports (50-100+ points per dungeon).

**Detection:** `points_remaining` dropping below 500 after a benchmark build, users seeing "rate limit exceeded" errors during subsequent coaching sessions.

**Phase relevance:** Benchmark aggregation phase must include budget-aware fetching from day one.

---

### Pitfall 3: Comparing Incomparable Key Levels

**What goes wrong:** A +10 key and a +15 key are fundamentally different encounters. Mobs have different health pools, damage output, and affix combinations. Telling a player "your DPS on this +10 is below the benchmark" when the benchmark was built from +14 data is misleading and demoralizing.

**Why it happens:** WCL rankings are partitioned by keystone level, but it's tempting to just grab "top rankings" without filtering by bracket. The default `characterRankings` query returns the highest-scoring players across all key levels, heavily biased toward the highest keys where players have better gear and more skill.

**Consequences:**
- Benchmarks skewed toward top-end players doing +14/+15 keys, meaningless for a player doing +8.
- DPS targets that are physically impossible at lower key levels (mobs die too fast to build sustained damage, or gear differences dominate).
- Advice like "you should use X cooldown rotation" that only works when trash lives long enough for multi-target ramp.

**Prevention:**
- Always filter rankings by bracket (keystone level) when building benchmarks. The `characterRankings` `bracket` parameter filters by keystone level for M+.
- If the player's key level is known, fetch benchmarks for that bracket +/- 1 level.
- If bracket data is sparse (low sample at exact key level), explicitly state: "benchmark from +12 data, your run was +10 — differences expected."
- Never present cross-bracket comparisons as direct performance gaps.

**Detection:** Benchmark DPS values that seem impossibly high for the player's key level, or advice that references mechanics/timings only present at higher key levels.

**Phase relevance:** Benchmark aggregation and gap analysis phases.

---

### Pitfall 4: M+ Data Quality — Depleted Keys, Abandoned Runs, Partial Logs

**What goes wrong:** Not all M+ runs in WCL are valid benchmark data. Depleted (over-time) keys, abandoned runs, partial uploads, and wipe-heavy runs all pollute benchmarks if not filtered.

**Why it happens:**
- WCL rankings already filter for timed keys (only timed runs appear in rankings), so rankings-sourced benchmarks are partially protected. But if you ever fetch runs from reports directly (e.g., for a specific player's history), you'll encounter untimed runs.
- Partial logs occur when the logger disconnects mid-run. These show up as runs with abnormally few segments or missing the aggregate fight.
- Some logs only contain a subset of the dungeon segments (logger joined mid-run, or combat log was reset).
- The existing `dungeon_analysis.py` already handles missing aggregate fights gracefully (`_get_run_fights` falls back to segments), but does not detect partial runs.

**Consequences:** Benchmarks polluted with low-quality data. "Average DPS" dragged down by wipe runs or inflated by partial-log runs where only the clean pulls are recorded.

**Prevention:**
- For benchmark building: only use WCL rankings data (pre-filtered for timed completions).
- For player log analysis: detect partial runs by checking segment count against expected dungeon layout (each dungeon has a known number of bosses — if fewer boss fights than expected, flag as partial).
- Add a `run_quality` field to analysis output: "timed", "depleted", "partial", "unknown".
- Use `keystoneBonus` from `ReportFight` fields (available in fight data) to determine if the run was timed (keystoneBonus >= 1) or depleted (keystoneBonus == 0). Note: the existing `_query_all_fights` in `dungeon_analysis.py` does NOT currently query `keystoneBonus` or `keystoneLevel` fields.

**Detection:** Runs with 0 boss kills, runs shorter than 5 minutes (likely partial), runs with keystoneBonus == 0 being mixed into benchmarks.

**Phase relevance:** Must be addressed when building both benchmarks (data quality filter) and player analysis (run classification).

---

### Pitfall 5: Benchmark Staleness — M+ Meta Shifts Faster Than Raid

**What goes wrong:** Cached benchmarks become stale quickly. M+ meta shifts with weekly affix rotations, hotfixes, and tier set/gear upgrades. A benchmark from 2 weeks ago may be meaningfully different from current optimal play.

**Why it happens:**
- Raid benchmarks are relatively stable: boss fights don't change week to week, and the meta shifts slowly (tier set acquisition over weeks).
- M+ meta shifts with: weekly affix rotations (different affixes favor different specs/builds), Blizzard hotfixes that nerf/buff dungeons or classes, and gradual gear inflation as the season progresses.
- The existing cache TTL is 6 hours for builds, 24 hours for encounters. These may be too long for M+ benchmarks in the first weeks of a season, and too short once the meta stabilizes.

**Consequences:**
- Stale benchmarks recommend talents/rotations that were optimal last week but not this week.
- DPS targets drift as average player gear improves through the season.
- Affix-specific strategies become irrelevant when affixes rotate.

**Prevention:**
- Use shorter cache TTLs for M+ benchmarks (2-4 hours) during early season, relaxing to 6-12 hours after 4+ weeks.
- Include affix awareness: store which affix set was active when benchmarks were built. Invalidate cache on affix rotation (weekly reset).
- Include a `benchmark_age` field in coaching output so Claude can tell the player "this benchmark is from 3 days ago, current meta may differ."
- Consider partition-aware caching: WCL partitions M+ rankings by season patches. Always query the current partition.

**Detection:** Coaching advice mentioning strategies for last week's affixes, DPS benchmarks that don't match current top logs when spot-checked.

**Phase relevance:** Cache design must account for this from the benchmark phase onward.

---

## Moderate Pitfalls

### Pitfall 6: Misunderstanding M+ DPS Metrics

**What goes wrong:** Presenting "overall DPS" (total damage / wall-clock time) as the primary metric, or confusing it with "active DPS" (total damage / combat time). Players and coaching tools frequently misinterpret these.

**Prevention:**
- The existing codebase already correctly uses active time (sum of fight segment durations) rather than wall-clock time (`dungeon_analysis.py:375-379`). Maintain this for all new M+ tools.
- Clearly label which metric is being shown: "Active DPS" (during combat only) vs "Overall DPS" (full run wall-clock).
- For benchmarks, use active DPS — it's comparable across runs with different route efficiencies and downtime.
- For coaching, also report "time between pulls" as a separate efficiency metric, not mixed into DPS.
- Be aware: WCL's own rankings use a specific calculation that may differ from both. When comparing player data against WCL rankings, use the same formula WCL uses (damage / total run time for the ranking metric).

**Phase relevance:** All M+ analysis phases.

---

### Pitfall 7: Per-Segment DPS Is Not Additive

**What goes wrong:** Summing or averaging per-segment DPS values to get "overall DPS." This is mathematically wrong because segments have different durations.

**Prevention:**
- Overall DPS = total damage across all segments / total active time across all segments.
- Per-segment DPS is useful for identifying weak pulls but NOT for computing overall performance.
- The existing `dungeon_analysis.py` correctly computes overall DPS from totals (`total_damage / active_time_sec` at line 500), but per-segment DPS is computed independently per segment. Ensure new tools don't accidentally average these.

**Phase relevance:** Gap analysis and segment-level coaching.

---

### Pitfall 8: Target Priority Blindness in DPS Comparison

**What goes wrong:** Comparing total DPS without considering target priority. In M+, a player doing 1.2M DPS but ignoring priority targets (inspiring mobs, spiteful shades, explosive orbs) is performing worse than a player doing 1.0M DPS on correct targets.

**Prevention:**
- DPS alone is insufficient for M+ coaching. Include damage breakdown by target type where possible.
- Track deaths and wipe causes alongside DPS — a dead DPS does zero DPS.
- For boss fights within M+, segment-level DPS comparison is more meaningful (bosses are consistent encounters).
- For trash, acknowledge that DPS varies wildly based on pull size and composition. Benchmark trash DPS per pull count (2-pack vs 4-pack pulls have very different DPS profiles).
- Consider tracking "damage to priority targets" as a separate metric if WCL data supports target-level breakdown.

**Phase relevance:** Gap analysis and coaching advice generation.

---

### Pitfall 9: Segment Classification Fragility

**What goes wrong:** The existing `_classify_segments` (dungeon_analysis.py:236) uses `encounterID > 0` to distinguish bosses from trash. This works for basic classification but can break with:
- Mini-bosses or rare spawns that may or may not have encounter IDs.
- Dungeon-specific scripted events (gauntlets, RP phases) that create fights with ambiguous encounter IDs.
- WCL occasionally misclassifying segments depending on logger addon version.

**Prevention:**
- Maintain a known-boss-encounter-ID list per dungeon for the current season.
- For segments that don't match known IDs, classify based on duration and damage patterns rather than assuming trash.
- Log unexpected encounterIDs for manual review rather than silently misclassifying.

**Phase relevance:** Segment-level analysis and benchmarking.

---

### Pitfall 10: Reusing Raid Tool Architecture Without Adaptation

**What goes wrong:** Copying the raid benchmark pattern (`query rankings -> fetch top N reports -> aggregate`) without adapting for M+ structural differences. Raid tools assume one fight per encounter; M+ has 15-30 fights per dungeon run.

**Why it happens:** Code reuse is tempting. The existing `_query_rankings` pattern in builds/rotation/timelines/defensives all follow the same shape. But M+ data needs:
- Multi-segment orchestration (the dungeon is the encounter, but analysis happens per-segment).
- Route awareness (different groups pull different packs in different orders).
- Full-run context (cooldown usage across the entire dungeon, not just one boss).

**Prevention:**
- Create M+-specific ranking/benchmark modules rather than parameterizing existing raid modules with `if is_dungeon: ...` branches.
- Share infrastructure (WCL client, cache, spec parsing, spell data) but separate orchestration logic.
- The existing `dungeon_analysis.py` is already a good pattern for M+-specific orchestration — extend it rather than adapting raid tools.

**Phase relevance:** Architecture decision for the entire M+ milestone.

---

## Minor Pitfalls

### Pitfall 11: Affix-Specific Advice Without Affix Detection

**What goes wrong:** Giving cooldown or defensive advice that's affix-dependent without knowing which affixes were active during the analyzed run or the benchmark run.

**Prevention:** Query affix information from the report's fight data (WCL `ReportFight` includes `keystoneAffixes`). Store affix set with benchmarks. At minimum, note "benchmark affixes may differ from your run" in coaching output.

**Phase relevance:** Defensive patterns and cooldown coaching.

---

### Pitfall 12: Chinese Localization for Dungeon/Segment Names

**What goes wrong:** WCL returns English dungeon and segment names. The existing system has a Chinese-name bridge for spells and talents but not for dungeon/zone names. M+ coaching will reference dungeon names frequently.

**Prevention:** Build a dungeon name localization map (small, static per season) or accept English dungeon names with Chinese coaching text. Inconsistent language mixing confuses users.

**Phase relevance:** All user-facing M+ output.

---

### Pitfall 13: GraphQL Query Cost Underestimation

**What goes wrong:** Each WCL GraphQL query costs points based on complexity. Queries that fetch `table()` data or `events()` data with wide time ranges cost significantly more than metadata queries. The existing codebase does not pre-estimate query cost.

**Prevention:** Document expected point costs per query type. The existing dungeon analysis header comment (`~5-7 WCL points` default, `+30-100 points` with casts) is a good pattern. Apply this discipline to all new M+ tools.

**Phase relevance:** All phases, especially benchmark building.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| M+ Benchmark Aggregation | Pitfall 1 (API structure), Pitfall 2 (rate limits), Pitfall 3 (key level mixing) | Build bracket-aware, budget-conscious fetching from day one |
| M+ Cooldown Timeline | Pitfall 10 (raid architecture reuse), Pitfall 8 (target priority) | M+-specific orchestration handling full-run context |
| M+ Rotation Profile | Pitfall 3 (key level comparison), Pitfall 6 (DPS metric confusion) | Active DPS with bracket filtering, per-segment breakdown |
| M+ Defensive Patterns | Pitfall 11 (affix blindness), Pitfall 9 (segment classification) | Affix-aware analysis, known-boss-ID validation |
| M+ Death Analysis | Pitfall 4 (partial logs), Pitfall 8 (target priority) | Run quality detection, damage-source correlation |
| M+ Gap Analysis | Pitfall 5 (staleness), Pitfall 7 (DPS arithmetic) | Fresh benchmarks, correct weighted averaging |

## Existing Codebase Strengths to Preserve

The current codebase already handles several potential pitfalls well:

1. **Active time calculation** (`dungeon_analysis.py:375-379`): Correctly sums segment durations rather than using wall-clock time.
2. **gameZone-based run grouping** (`dungeon_analysis.py:118-154`): Robust dungeon run detection that handles multi-dungeon reports.
3. **Rate limit logging** (`wcl_client.py:182-193`): Foundation exists for budget tracking (needs enforcement, not just logging).
4. **Segment-level DPS gating** (`dungeon_analysis.py:418`): Only queries per-segment DPS when segment count is manageable (<=10), avoiding API waste.
5. **Parallel query pattern** (`dungeon_analysis.py:344-347, 382-394`): Efficient asyncio.gather usage that should be replicated in new tools.

## Sources

- [WarcraftLogs API v2 Documentation](https://www.warcraftlogs.com/v2-api-docs/warcraft/)
- [WarcraftLogs Rankings and Parses Guide](https://www.warcraftlogs.com/help/ranks/)
- [WCL ReportFight Schema](https://www.warcraftlogs.com/v2-api-docs/warcraft/reportfight.doc.html) (keystoneLevel, keystoneBonus, keystoneAffixes fields)
- [WCL Encounter Schema](https://www.warcraftlogs.com/v2-api-docs/warcraft/encounter.doc.html) (characterRankings with bracket parameter)
- [WCL M+ Rankings Discussion](https://forums.combatlogforums.com/t/mythic-dungeons-rankings-discussion/662) (partition by keystone level, affix combo brackets)
- [WCL Rate Limit Forum](https://forums.combatlogforums.com/t/api-rate-limit-and-points-spent/10320) (3600 points/hour, 1-hour cycle reset)
- [Keystone Heroes (archived)](https://github.com/ljosberinn/keystone-heroes) (M+ analysis patterns, route/cooldown analysis)
- [Active DPS Explanation](https://onlyfarms.gg/wiki/world-of-warcraft/active-dps) (active vs overall DPS distinction)
- Codebase analysis: `src/tools/builds.py`, `src/tools/dungeon_analysis.py`, `src/wcl_client.py`, `src/cache.py`
