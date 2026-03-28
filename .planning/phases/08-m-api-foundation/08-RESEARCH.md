# Phase 8: M+ API Foundation - Research

**Researched:** 2026-03-28
**Domain:** WCL GraphQL API for Mythic+ rankings, Pydantic models, cache strategy
**Confidence:** MEDIUM-HIGH (difficulty=10 confirmed via v1 docs; bracket parameter behavior needs live verification)

## Summary

Phase 8 is a foundation phase that verifies WCL M+ API parameters work correctly, adds `mythic_plus` difficulty support to existing infrastructure, creates new Pydantic models for M+ benchmarks, and establishes cache key strategy. No user-facing MCP tools are added. All downstream M+ phases (9-11) depend on these patterns being verified.

The critical finding: WCL v1 API documentation explicitly lists `difficulty: 10` as "Challenge Mode" (Mythic+ is the successor to Challenge Mode). This raises confidence from MEDIUM to MEDIUM-HIGH. The `bracket` parameter for M+ filters by keystone level (confirmed by WCL rankings help page). The `ReportFight` type includes `keystoneLevel`, `keystoneBonus`, `keystoneAffixes`, and `keystoneTime` fields (confirmed by WCL v2 schema docs and community projects).

**Primary recommendation:** Write a verification script that queries WCL live with `difficulty: 10` and `bracket` parameters, print response structure, then build code only after verification passes. This script-first approach avoids building on unverified assumptions.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Script-first API verification approach: write a verification script that queries WCL live with M+ parameters (difficulty=10, bracket filtering, dungeon encounter IDs), prints the response structure, and documents results. Build code only after verification passes.
- **D-02:** Accept raw integer for key level (e.g., `bracket=10` for a +10 key). If a bracket has sparse data (fewer than 3 players), fall back to adjacent bracket (e.g., +10 -> try +11, then +9) and disclose the bracket gap in output.
- **D-03:** Do NOT normalize or aggregate across key levels -- always compare within the same bracket.
- **D-04:** Use 5 top players for M+ benchmarks (not 50 like raid). Rationale: M+ reports are larger (30+ min, many segments), so each report costs more API points. 5 players gives sufficient signal while keeping rate limit budget manageable (~20-50 points per dungeon benchmark).

### Claude's Discretion
- API verification script structure and error handling
- Exact Pydantic model field names (follow existing naming conventions)
- Cache TTL for M+ benchmarks (research suggests 6h, align with raid benchmark TTL)
- Whether to add `keystoneLevel`/`keystoneBonus` fields to `_query_all_fights` in this phase or Phase 9

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BENCH-01 | Agent can query WCL M+ leaderboard for top DPS by dungeon+spec+key level | `characterRankings(difficulty: 10, bracket: N)` query pattern; DIFFICULTY_MAP extension; encounter ID discovery via `get_encounters(content_type="mythic_plus")` |
| BENCH-04 | Benchmark data cached per dungeon+spec+key level | Cache key format `mplus_bench:{spec}:{encounter_id}:{key_level}` with 6h TTL; existing `cache_get`/`cache_set` infrastructure |
</phase_requirements>

## Standard Stack

### Core (Unchanged -- No New Dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12+ | Runtime | Already in use |
| httpx | Latest | WCL GraphQL transport | Already in use |
| Pydantic v2 | >=2.0 | Data models | Already in use |
| pytest | >=8.0 | Testing | Already in use |
| pytest-asyncio | >=0.24 | Async test support | Already in use |

### No New Dependencies Needed

This phase is purely additive: one constant addition to `DIFFICULTY_MAP`, new Pydantic models, cache key strategy, and a verification script. Zero new packages required.

## Architecture Patterns

### Recommended Changes

```
src/
  tools/
    builds.py            # MODIFY: add "mythic_plus": 10 to DIFFICULTY_MAP
    dungeon_analysis.py   # MODIFY: add keystoneLevel/keystoneBonus to _query_all_fights
  models.py              # MODIFY: add MplusRankingEntry, MplusBenchmarkMeta
scripts/
  verify_mplus_api.py    # NEW: verification script (not shipped, dev-only)
tests/
  test_mplus_foundation.py  # NEW: unit tests for M+ foundation
```

### Pattern 1: DIFFICULTY_MAP Extension

**What:** Add `"mythic_plus": 10` to the existing difficulty mapping constant.
**When to use:** Whenever any tool needs to query M+ rankings via `characterRankings`.
**Example:**
```python
# Source: src/tools/builds.py line 49
DIFFICULTY_MAP: dict[str, int] = {
    "normal": 3,
    "heroic": 4,
    "mythic": 5,
    "mythic_plus": 10,  # M+ / Challenge Mode
}
```

### Pattern 2: M+ Rankings Query (with bracket)

**What:** Same `characterRankings` query pattern as raid, but with `difficulty: 10` and optional `bracket` for keystone level filtering.
**When to use:** Querying M+ leaderboard data.
**Example:**
```python
# Adapted from existing _query_rankings in timelines.py
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

### Pattern 3: KeystoneLevel/KeystoneBonus in Fight Queries

**What:** Extend `_query_all_fights` to include `keystoneLevel`, `keystoneBonus`, `keystoneAffixes`, `keystoneTime` fields.
**When to use:** Detecting run quality (timed/depleted), filtering benchmark data.
**Example:**
```python
# Addition to _query_all_fights GraphQL in dungeon_analysis.py
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
                keystoneLevel
                keystoneBonus
                keystoneAffixes
                keystoneTime
                gameZone {{ id name }}
            }}
            title
        }}
    }}
"""
```

### Pattern 4: Cache Key Strategy for M+ Benchmarks

**What:** Cache keys must include dungeon encounter ID, spec, and key level to avoid cross-bracket contamination.
**Example:**
```python
# 缓存键格式
cache_key = f"mplus_bench:{spec}:{encounter_id}:k{key_level}"
# 示例: "mplus_bench:frost-mage:12811:k10"
```

### Anti-Patterns to Avoid
- **Reusing raid rankings query without bracket:** Default M+ rankings return top keys (14+), useless for comparing a +8 player.
- **Treating `bracketData` as item level:** For M+ rankings, `bracketData` is keystone level (8-15), not item level (600+).
- **Building a separate WCL client for M+:** Same API, same auth, same rate limits. Use existing `WCLClient`.
- **Querying all 8 dungeons eagerly:** ~160-720 points. Build per-dungeon, on-demand.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| M+ rankings query | New query framework | Extend existing `_query_rankings` pattern from `timelines.py` | Same GraphQL structure, only difficulty and bracket differ |
| Dungeon encounter discovery | Manual encounter ID table | `get_encounters(content_type="mythic_plus")` | Already handles zone filtering with heuristic (1-2 encounters = dungeon) |
| Spec parsing | New spec mapper | Existing `SPEC_MAPPING` in `builds.py` | 39-spec mapping, well-tested |
| Cache implementation | New cache backend | Existing `cache_get`/`cache_set` in `cache.py` | File-based JSON cache with TTL, works at this scale |

**Key insight:** This phase requires almost no new infrastructure. The M+ query pattern is structurally identical to raid queries with different parameter values. The primary risk is incorrect parameter values, not missing infrastructure.

## Common Pitfalls

### Pitfall 1: difficulty: 10 Not Working

**What goes wrong:** The `difficulty: 10` value might not work in v2 GraphQL for M+ rankings, returning empty results or an error.
**Why it happens:** The value 10 is confirmed in v1 API docs as "Challenge Mode" but v2 GraphQL schema documentation is behind a paywall/auth wall and cannot be directly verified.
**How to avoid:** The verification script (D-01) tests this live before any code is built. Fallback: try querying without `difficulty` parameter to see what WCL returns for dungeon encounters by default.
**Warning signs:** Empty `rankings` array when querying a popular spec/dungeon combo that should have data.

### Pitfall 2: Bracket Parameter Format

**What goes wrong:** `bracket: 12` might not work as a raw integer. WCL might expect a different encoding for M+ keystone brackets.
**Why it happens:** The WCL rankings help page says "brackets are keystone levels for M+ dungeons" but the exact GraphQL parameter format is not verified.
**How to avoid:** Verification script tests `bracket: 12` (popular key level with lots of data). If it fails, try omitting bracket and inspecting the `bracketData` field in returned rankings to understand the bracket encoding.
**Warning signs:** Rankings returning data from all key levels instead of the requested bracket.

### Pitfall 3: Dungeon Encounter ID Discovery

**What goes wrong:** `get_encounters(content_type="mythic_plus")` might not return the correct encounter IDs for M+ dungeons, or the encounter IDs might not work with `characterRankings`.
**Why it happens:** The filter in `encounters.py` uses a heuristic: zones with 1-2 encounters are classified as dungeons. Some M+ season zones might have zero encounters listed, or the encounter IDs might differ from what `characterRankings` expects.
**How to avoid:** Verification script first calls `get_encounters(content_type="mythic_plus")` to get encounter IDs, then tests one with `characterRankings`. If encounter discovery fails, try querying WCL for known M+ zone IDs from the current season.
**Warning signs:** `get_encounters(content_type="mythic_plus")` returning zero zones, or `characterRankings` returning empty for a discovered encounter ID.

### Pitfall 4: keystoneLevel Field Availability

**What goes wrong:** Adding `keystoneLevel` and `keystoneBonus` to the fights query might fail if these fields are only populated for certain fight types or are null for some fights.
**Why it happens:** WCL `ReportFight` type has these fields documented, but they may only be populated on the aggregate fight (encounterID > 0), not on segment fights (encounterID == 0).
**How to avoid:** Verification script queries a known M+ report and checks which fights have `keystoneLevel` populated. Handle null gracefully with `f.get("keystoneLevel", 0)`.
**Warning signs:** All `keystoneLevel` values being 0 or null.

### Pitfall 5: Sparse Brackets at Low Key Levels

**What goes wrong:** Querying `bracket: 6` for a niche spec returns fewer than 3 rankings, making benchmarks unreliable.
**Why it happens:** Low key levels have fewer WCL-logged players. Off-meta specs at low keys are very sparse.
**How to avoid:** Per D-02, implement adjacent bracket fallback: if results < 3, try bracket +/- 1. Always disclose when using a different bracket. Set minimum sample threshold (3 players).
**Warning signs:** Rankings response with `count: 0` or `count: 1-2`.

## Code Examples

### Verification Script Structure

```python
#!/usr/bin/env python3
"""
验证 WCL M+ API 参数 — Phase 8 前置验证。

验证项:
1. difficulty: 10 是否返回 M+ 排行数据
2. bracket 参数是否接受原始整数
3. 副本遭遇 ID 是否可从 get_encounters 发现
4. ReportFight 的 keystoneLevel/keystoneBonus 字段
"""
import asyncio
import json
import os

from src.wcl_client import WCLClient
from src.tools.encounters import get_encounters


async def verify():
    client = WCLClient(
        client_id=os.environ["WCL_CLIENT_ID"],
        client_secret=os.environ["WCL_CLIENT_SECRET"],
    )

    # Step 1: 发现 M+ 副本遭遇 ID
    encounters = await get_encounters(client, content_type="mythic_plus")
    print("=== M+ Zones ===")
    for zone in encounters.zones:
        print(f"  {zone.name} (id={zone.id})")
        for enc in zone.encounters:
            print(f"    Encounter: {enc.name} (id={enc.id})")

    if not encounters.zones or not encounters.zones[0].encounters:
        print("ERROR: No M+ encounters found")
        return

    # 使用第一个副本的第一个遭遇 ID
    test_enc_id = encounters.zones[0].encounters[0].id
    print(f"\n=== Testing encounter ID: {test_enc_id} ===")

    # Step 2: 测试 difficulty: 10
    gql_no_bracket = f"""
        worldData {{
            encounter(id: {test_enc_id}) {{
                name
                characterRankings(
                    className: "Mage"
                    specName: "Frost"
                    metric: dps
                    difficulty: 10
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql_no_bracket)
    enc = data.get("worldData", {}).get("encounter", {})
    cr = enc.get("characterRankings", {})
    print(f"\n=== difficulty:10 结果 ===")
    print(f"  encounter name: {enc.get('name')}")
    print(f"  rankings count: {cr.get('count', 0)}")
    print(f"  hasMorePages: {cr.get('hasMorePages')}")
    if cr.get("rankings"):
        r0 = cr["rankings"][0]
        print(f"  first ranking: {json.dumps(r0, indent=2)}")
        print(f"  bracketData (should be key level): {r0.get('bracketData')}")

    # Step 3: 测试 bracket 参数
    gql_with_bracket = f"""
        worldData {{
            encounter(id: {test_enc_id}) {{
                name
                characterRankings(
                    className: "Mage"
                    specName: "Frost"
                    metric: dps
                    difficulty: 10
                    bracket: 12
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data2 = await client.query(gql_with_bracket)
    cr2 = data2.get("worldData", {}).get("encounter", {}).get("characterRankings", {})
    print(f"\n=== bracket:12 结果 ===")
    print(f"  rankings count: {cr2.get('count', 0)}")
    if cr2.get("rankings"):
        r1 = cr2["rankings"][0]
        print(f"  first ranking bracketData: {r1.get('bracketData')}")

    # Step 4: 测试 keystoneLevel/keystoneBonus
    if cr.get("rankings"):
        report_code = cr["rankings"][0].get("report", {}).get("code")
        if report_code:
            gql_fights = f"""
                reportData {{
                    report(code: "{report_code}") {{
                        fights {{
                            id
                            encounterID
                            name
                            keystoneLevel
                            keystoneBonus
                            keystoneAffixes
                            keystoneTime
                            gameZone {{ id name }}
                        }}
                    }}
                }}
            """
            data3 = await client.query(gql_fights)
            fights = data3.get("reportData", {}).get("report", {}).get("fights", [])
            print(f"\n=== ReportFight keystoneLevel 测试 ===")
            for f in fights[:5]:
                print(f"  fight {f.get('id')}: "
                      f"encounterID={f.get('encounterID')} "
                      f"keystoneLevel={f.get('keystoneLevel')} "
                      f"keystoneBonus={f.get('keystoneBonus')} "
                      f"keystoneAffixes={f.get('keystoneAffixes')}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(verify())
```

### New Pydantic Models (Minimal for Phase 8)

```python
# 添加到 src/models.py

class MplusRankingEntry(BaseModel):
    """M+ 排行榜单条记录 — 从 characterRankings 解析。"""

    name: str
    class_name: str = Field(default="", alias="class")
    spec: str = ""
    amount: float = Field(description="DPS 数值")
    duration: int = Field(description="副本总时长（毫秒）")
    report_code: str = ""
    fight_id: int = 0
    bracket_data: int = Field(
        default=0,
        description="M+ 钥石等级（非装等）",
    )

    model_config = {"populate_by_name": True}


class MplusBenchmarkMeta(BaseModel):
    """M+ 基准元数据 — 记录基准构建参数。"""

    encounter_id: int
    encounter_name: str = ""
    spec: str
    key_level: int
    actual_bracket: int = Field(
        default=0,
        description="实际使用的 bracket（fallback 时可能与 key_level 不同）",
    )
    sample_size: int = 0
    median_dps: float = 0.0
    dps_p25: float = 0.0
    dps_p75: float = 0.0
    cached_at: str = ""
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Challenge Mode (difficulty=10) | Mythic+ (still difficulty=10) | WoD -> Legion (2016) | Same API constant, different game mode name |
| v1 REST API | v2 GraphQL API | ~2020 | Same difficulty values, different query syntax |
| No keystone fields in fight data | `keystoneLevel`/`keystoneBonus`/`keystoneAffixes`/`keystoneTime` in `ReportFight` | v2 API | Enables run quality detection |

## Open Questions

1. **bracket parameter exact behavior**
   - What we know: WCL rankings help page says "brackets are keystone levels for M+ dungeons". v1 API shows bracket as an integer parameter.
   - What's unclear: Whether v2 GraphQL `bracket` accepts raw keystone level integer (e.g., `bracket: 12`) or uses some other encoding.
   - Recommendation: Verification script tests this (Step 3). If raw integer fails, inspect `bracketData` values in unfiltered rankings to determine encoding.

2. **M+ dungeon encounter IDs for current season**
   - What we know: `get_encounters(content_type="mythic_plus")` uses heuristic (1-2 encounters per zone = dungeon).
   - What's unclear: Whether current TWW M+ season zones appear correctly in `worldData.expansion.zones`.
   - Recommendation: Verification script tests this (Step 1). If no zones appear, investigate M+ season zone structure (WCL may organize M+ dungeons under a separate "M+ Season N" zone).

3. **keystoneLevel populated on which fights?**
   - What we know: `ReportFight` type has `keystoneLevel` field (WCL v2 schema docs).
   - What's unclear: Whether only the aggregate fight (encounterID > 0) has this field populated, or all segment fights do.
   - Recommendation: Verification script tests this (Step 4). Handle nulls gracefully.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio |
| Config file | `pyproject.toml` ([tool.pytest.ini_options]) |
| Quick run command | `python -m pytest tests/test_mplus_foundation.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BENCH-01 | M+ rankings query with difficulty=10 and bracket filter | unit | `python -m pytest tests/test_mplus_foundation.py::test_mplus_rankings_query -x` | Wave 0 |
| BENCH-01 | DIFFICULTY_MAP includes mythic_plus -> 10 | unit | `python -m pytest tests/test_mplus_foundation.py::test_difficulty_map_mythic_plus -x` | Wave 0 |
| BENCH-01 | Encounter discovery for M+ dungeons | unit | `python -m pytest tests/test_mplus_foundation.py::test_mplus_encounter_discovery -x` | Wave 0 |
| BENCH-04 | Cache key includes dungeon+spec+key_level | unit | `python -m pytest tests/test_mplus_foundation.py::test_mplus_cache_key_strategy -x` | Wave 0 |
| BENCH-04 | Cache TTL is 6 hours | unit | `python -m pytest tests/test_mplus_foundation.py::test_mplus_cache_ttl -x` | Wave 0 |
| BENCH-01 | Sparse bracket fallback to adjacent | unit | `python -m pytest tests/test_mplus_foundation.py::test_sparse_bracket_fallback -x` | Wave 0 |
| n/a | keystoneLevel/keystoneBonus in fight queries | unit | `python -m pytest tests/test_mplus_foundation.py::test_keystone_fields_in_fights -x` | Wave 0 |
| n/a | New Pydantic models validate correctly | unit | `python -m pytest tests/test_mplus_foundation.py::test_mplus_models -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_mplus_foundation.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_mplus_foundation.py` -- covers BENCH-01, BENCH-04, keystone fields, models
- [ ] `tests/fixtures/wcl_responses.py` -- add M+ ranking mock response data (extend existing file)

## Sources

### Primary (HIGH confidence)
- WCL v1 API documentation (`https://www.warcraftlogs.com/v1/docsjson`) -- explicitly lists `difficulty: 10` as "Challenge Mode" (M+ successor). Verified via WebFetch.
- WCL Rankings help page (`https://www.warcraftlogs.com/help/ranks/`) -- confirms "brackets are keystone levels for Mythic dungeons".
- Existing codebase: `src/tools/builds.py` (DIFFICULTY_MAP, SPEC_MAPPING, _query_rankings pattern)
- Existing codebase: `src/tools/timelines.py` (benchmark pipeline: rankings -> reports -> events -> aggregate)
- Existing codebase: `src/tools/dungeon_analysis.py` (_query_all_fights, DungeonRun, gameZone grouping)
- Existing codebase: `src/tools/encounters.py` (_filter_dungeon_zones heuristic)
- Existing codebase: `src/cache.py` (cache_get/cache_set with TTL)
- Existing codebase: `src/models.py` (DungeonRunAnalysisResponse, FightSegmentSummary)

### Secondary (MEDIUM confidence)
- WCL v2 ReportFight schema docs (`https://www.warcraftlogs.com/v2-api-docs/warcraft/reportfight.doc.html`) -- lists `keystoneLevel`, `keystoneBonus`, `keystoneAffixes`, `keystoneTime` fields. Page is 403 for direct fetch but confirmed by WebSearch result snippets.
- WCL v2 Encounter docs (`https://www.warcraftlogs.com/v2-api-docs/warcraft/encounter.doc.html`) -- characterRankings field exists on Encounter type.
- Community projects: `keystone-heroes` (`https://github.com/ljosberinn/keystone-heroes`) -- M+ analysis tool using WCL API, validates the general approach.
- `.planning/research/STACK.md` -- prior milestone research on M+ API patterns.

### Tertiary (LOW confidence)
- `bracket` parameter accepting raw keystone integer in v2 GraphQL -- inferred from v1 API docs and WCL help page, not live-verified.
- `keystoneLevel` field populated on segment fights (not just aggregate) -- inferred from schema docs, not live-verified.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, existing infrastructure proven
- Architecture: HIGH -- extension of well-understood patterns (DIFFICULTY_MAP, cache keys, Pydantic models)
- API parameters (difficulty=10): MEDIUM-HIGH -- confirmed in v1 docs as "Challenge Mode"; highly likely to work in v2
- API parameters (bracket): MEDIUM -- documented in help page but not live-verified in v2 GraphQL
- Pitfalls: HIGH -- derived from direct codebase analysis + official docs

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable domain, WCL API rarely changes)
