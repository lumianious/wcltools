"""
analyze_player_log 工具 — 分析玩家个人 WCL 日志。

收集指定玩家在某场战斗中的施法、Buff、天赋、死亡数据，
获取基准数据（循环/冷却/天赋/防御），对比产生结构化差距分析。

WCL 数据流:
  1. report.fights → startTime, endTime, kill, encounterID, name
  2. report.masterData → actors + ability name map → sourceID
  3. report.events(Casts) → 玩家施法事件（分页）
  4. report.table(Buffs) → Buff 覆盖率
  5. report.events(CombatantInfo) → 天赋信息
  6. report.events(Deaths) → 死亡事件

基准数据通过已有工具函数获取（6 小时缓存）。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import logging
import re
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.data import get_spell_name, get_talent_name
from src.models import (
    BuildDivergence,
    CooldownIssue,
    DefensiveIssue,
    PlayerAnalysisResponse,
    SpellGap,
)
from src.tools.builds import get_top_builds
from src.tools.defensives import get_defensive_patterns
from src.tools.rotation import (
    _find_actor_id,
    _query_buff_table,
    _query_cast_events,
    _query_master_data,
    get_rotation_profile,
)
from src.tools.timelines import get_cooldown_timelines
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
# 从 WCL URL 中提取 report code 的正则
_URL_PATTERN = re.compile(
    r"warcraftlogs\.com/reports/([A-Za-z0-9]+)"
)


# ============================================================
# URL / Report Code 解析
# ============================================================


def _extract_report_code(report: str) -> str:
    """
    从 report code 或完整 WCL URL 中提取 report code。

    支持:
      - "ABC123"
      - "https://www.warcraftlogs.com/reports/ABC123#fight=3"
    """
    report = report.strip()
    match = _URL_PATTERN.search(report)
    if match:
        return match.group(1)
    # 假设是纯 report code
    return report


# ============================================================
# 玩家名称匹配（大小写不敏感）
# ============================================================


def _find_actor_id_ci(
    actors: list[dict], player_name: str
) -> Optional[int]:
    """
    大小写不敏感地在 actors 中查找玩家 sourceID。

    先尝试精确匹配（复用 rotation._find_actor_id），
    失败后降级为大小写不敏感匹配。
    """
    # 精确匹配
    exact = _find_actor_id(actors, player_name)
    if exact is not None:
        return exact
    # 大小写不敏感
    lower_name = player_name.lower()
    for actor in actors:
        if actor.get("name", "").lower() == lower_name:
            return actor.get("id")
    return None


# ============================================================
# WCL 查询: 战斗信息（含 encounterID）
# ============================================================


async def _query_fight_info_full(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> dict[str, Any]:
    """
    查询指定战斗的完整信息（含 encounterID）。

    返回 {startTime, endTime, kill, encounterID, name}
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights(fightIDs: [{fight_id}]) {{
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
    fights = report.get("fights", [])
    return fights[0] if fights else {}


# ============================================================
# WCL 查询: CombatantInfo（天赋）
# ============================================================


async def _query_combatant_info(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """
    查询玩家的 CombatantInfo 事件，提取天赋列表。

    WCL CombatantInfo 包含 talents: [{id, ...}]
    返回原始事件列表。
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                events(
                    startTime: {start_time}
                    endTime: {end_time}
                    sourceID: {source_id}
                    dataType: CombatantInfo
                    limit: 100
                ) {{
                    data
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    return report.get("events", {}).get("data", [])


# ============================================================
# WCL 查询: 死亡事件
# ============================================================


async def _query_death_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """
    查询指定玩家的死亡事件。

    返回死亡事件列表 [{timestamp, ...}]
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                events(
                    startTime: {start_time}
                    endTime: {end_time}
                    sourceID: {source_id}
                    dataType: Deaths
                    limit: 100
                ) {{
                    data
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    return report.get("events", {}).get("data", [])


# ============================================================
# WCL 查询: 伤害总量
# ============================================================


async def _query_damage_done(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> float:
    """查询玩家总伤害量，返回浮点数。"""
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
    try:
        data = await client.query(gql)
        table = data.get("reportData", {}).get("report", {}).get("table", {})
        table_data = table.get("data", {})
        # WCL table 将伤害分布在 entries[] 中，求和
        entries = table_data.get("entries", [])
        total = sum(e.get("total", 0) for e in entries)
        return float(total)
    except Exception as exc:
        logger.warning("伤害总量查询失败 %s: %s", report_code, exc)
        return 0.0


# ============================================================
# 玩家数据收集
# ============================================================


async def _collect_player_fight_data(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    player_name: str,
) -> dict[str, Any]:
    """
    收集玩家在指定战斗中的全部数据。

    返回:
      {
        "fight_info": {startTime, endTime, kill, encounterID, name},
        "source_id": int,
        "ability_map": {gameID: name},
        "spell_counts": {spell_id: count},
        "spell_names": {spell_id: name},
        "buff_uptimes": [aura_dict, ...],
        "talents": [{id, ...}, ...],
        "deaths": [{timestamp, ...}, ...],
        "fight_duration": float (秒),
      }
    """
    # Step 1: 战斗信息
    fight_info = await _query_fight_info_full(
        client, report_code, fight_id
    )
    if not fight_info:
        raise ValueError(
            f"未找到战斗 fight_id={fight_id} in report {report_code}"
        )

    start_time = fight_info.get("startTime", 0)
    end_time = fight_info.get("endTime", 0)
    if not start_time or not end_time:
        raise ValueError("战斗缺少有效的起止时间")

    fight_duration = (end_time - start_time) / 1000.0

    # Step 2: masterData — actors + ability map
    actors, ability_map = await _query_master_data(client, report_code)
    source_id = _find_actor_id_ci(actors, player_name)
    if source_id is None:
        raise ValueError(
            f"未找到玩家 '{player_name}' in report {report_code}"
        )

    # Step 3-7: 并行查询施法/Buff/天赋/死亡/伤害
    cast_task = _query_cast_events(
        client, report_code, start_time, end_time, source_id
    )
    buff_task = _query_buff_table(
        client, report_code, start_time, end_time, source_id
    )
    damage_task = _query_damage_done(
        client, report_code, start_time, end_time, source_id
    )
    talent_task = _query_combatant_info(
        client, report_code, start_time, end_time, source_id
    )
    death_task = _query_death_events(
        client, report_code, start_time, end_time, source_id
    )

    events, auras, total_damage, combatant_events, death_events = (
        await asyncio.gather(
            cast_task, buff_task, damage_task, talent_task, death_task,
        )
    )

    # 统计施法次数
    spell_counts: dict[int, int] = defaultdict(int)
    spell_names: dict[int, str] = {}
    for evt in events:
        if evt.get("type") != "cast":
            continue
        spell_id = evt.get("abilityGameID")
        if spell_id:
            spell_counts[spell_id] += 1
            if spell_id not in spell_names:
                resolved = (
                    ability_map.get(spell_id)
                    or get_spell_name(spell_id)
                    or f"Spell {spell_id}"
                )
                spell_names[spell_id] = resolved

    # 提取天赋 ID 列表（TWW 使用 talentTree 而非 talents）
    talents: list[dict] = []
    if combatant_events:
        ci = combatant_events[0]
        talents = ci.get("talentTree", []) or ci.get("talents", [])

    # 计算 DPS
    player_dps = total_damage / fight_duration if fight_duration > 0 else 0.0

    return {
        "fight_info": fight_info,
        "source_id": source_id,
        "ability_map": ability_map,
        "spell_counts": dict(spell_counts),
        "spell_names": spell_names,
        "buff_uptimes": auras,
        "talents": talents,
        "deaths": death_events,
        "fight_duration": fight_duration,
        "player_dps": player_dps,
    }


# ============================================================
# 对比分析: 循环差距
# ============================================================


def _compare_rotation(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    fight_duration: float,
    rotation_bench: Any,
) -> list[SpellGap]:
    """
    将玩家施法数据与基准循环数据对比，产生 SpellGap 列表。

    基准来自 RotationProfileResponse.top_spells。
    """
    gaps: list[SpellGap] = []
    dur_min = fight_duration / 60.0 if fight_duration > 0 else 1.0

    # 构建反向名称索引: name → (spell_id, count)
    name_to_player: dict[str, tuple[int, int]] = {}
    for sid, name in player_spell_names.items():
        count = player_spell_counts.get(sid, 0)
        lower = name.lower()
        if lower not in name_to_player or count > name_to_player[lower][1]:
            name_to_player[lower] = (sid, count)

    for spell_stat in rotation_bench.top_spells:
        sid = spell_stat.spell_id
        player_casts = player_spell_counts.get(sid, 0)

        # 如果 spell ID 直接匹配不到，按名称回退匹配
        if player_casts == 0 and spell_stat.name:
            match = name_to_player.get(spell_stat.name.lower())
            if match:
                sid, player_casts = match

        player_cpm = round(player_casts / dur_min, 2)

        # 确定百分位桶
        p = spell_stat.percentiles
        p25 = p.get("p25", 0.0)
        p50 = p.get("p50", 0.0)
        p75 = p.get("p75", 0.0)

        if player_casts < p25:
            percentile = "below_p25"
            verdict = "undercast"
        elif player_casts < p50:
            percentile = "p25_p50"
            verdict = "ok"
        elif player_casts < p75:
            percentile = "p50_p75"
            verdict = "ok"
        else:
            percentile = "above_p75"
            verdict = "ok"

        name = (
            player_spell_names.get(sid)
            or spell_stat.name
            or f"Spell {sid}"
        )

        gaps.append(SpellGap(
            name=name,
            spell_id=sid,
            player_casts=player_casts,
            player_cpm=player_cpm,
            benchmark_median=spell_stat.total_casts,
            benchmark_cpm=spell_stat.cpm,
            percentile=percentile,
            verdict=verdict,
        ))

    return gaps


# ============================================================
# 对比分析: 冷却技能差距
# ============================================================


def _compare_cooldowns(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    timeline_bench: Any,
) -> list[CooldownIssue]:
    """
    将玩家冷却技能使用与基准时间线对比。

    基准来自 CooldownTimelineResponse.abilities。
    """
    issues: list[CooldownIssue] = []

    for ability in timeline_bench.abilities:
        # ability.total_casts 是 dict: {median, min, max}
        median_casts = ability.total_casts.get("median", 0.0)

        # 通过名称或 ability 中的信息匹配 spell_id
        # CooldownTimelineResponse 的 AbilityTimeline 没有 spell_id
        # 需要从 player_spell_names 中按名称反查
        matched_sid = _match_ability_spell_id(
            ability.name, player_spell_names
        )

        player_casts = 0
        if matched_sid:
            player_casts = player_spell_counts.get(matched_sid, 0)

        missed = max(0, int(median_casts - player_casts))

        if missed > 0:
            issues.append(CooldownIssue(
                name=ability.name,
                spell_id=matched_sid or 0,
                player_casts=player_casts,
                benchmark_median_casts=median_casts,
                missed_uses=missed,
            ))

    return issues


def _match_ability_spell_id(
    ability_name: str,
    spell_names: dict[int, str],
) -> Optional[int]:
    """按名称反查 spell_id（大小写不敏感）。"""
    lower_name = ability_name.lower()
    for sid, name in spell_names.items():
        if name.lower() == lower_name:
            return sid
    return None


# ============================================================
# 对比分析: 防御技能差距
# ============================================================


def _compare_defensives(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    defensive_bench: Any,
) -> list[DefensiveIssue]:
    """
    将玩家防御技能使用与基准对比。

    基准来自 DefensivePatternResponse.defensive_timings。
    """
    issues: list[DefensiveIssue] = []

    for timing in defensive_bench.defensive_timings:
        sid = timing.spell_id
        player_casts = player_spell_counts.get(sid, 0)
        player_used = player_casts > 0

        if timing.usage_rate > 50.0 and not player_used:
            verdict = "unused"
        elif timing.usage_rate > 50.0 and player_casts < len(timing.clusters):
            verdict = "underused"
        else:
            verdict = "ok"

        issues.append(DefensiveIssue(
            name=timing.name,
            spell_id=sid,
            player_used=player_used,
            player_cast_count=player_casts,
            benchmark_usage_rate=timing.usage_rate,
            verdict=verdict,
        ))

    return issues


# ============================================================
# 对比分析: 天赋构建差异
# ============================================================


def _compare_build(
    player_talents: list[dict],
    build_bench: Any,
) -> BuildDivergence:
    """
    将玩家天赋与热门构建对比。

    基准来自 TopBuildsResponse.builds。
    player_talents: [{id, ...}] — CombatantInfo 中的天赋列表。
    """
    if not player_talents or not build_bench.builds:
        return BuildDivergence()

    # 提取玩家天赋 ID 集合（使用 WCL entry ID 以匹配 talent_import 格式）
    player_ids: set[int] = set()
    # 同时建立 entry_id → node_id 映射，用于名称解析
    entry_to_node: dict[int, int] = {}
    for t in player_talents:
        tid = t.get("id") or t.get("talentID")
        nid = t.get("nodeID")
        if tid:
            player_ids.add(tid)
            if nid:
                entry_to_node[tid] = nid

    if not player_ids:
        return BuildDivergence()

    # 从每个 build 的 talent_import 字符串中提取天赋 ID 集合
    # talent_import 格式: "96161:2,96165:1,..."
    best_idx = 0
    best_overlap = 0.0

    build_talent_sets: list[set[int]] = []
    for i, build in enumerate(build_bench.builds):
        build_ids: set[int] = set()
        for entry in build.talent_import.split(","):
            parts = entry.strip().split(":")
            if parts and parts[0].isdigit():
                build_ids.add(int(parts[0]))
        build_talent_sets.append(build_ids)

        # 计算重叠度
        if build_ids:
            overlap = len(player_ids & build_ids) / len(
                player_ids | build_ids
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i

    # 找出差异
    best_set = build_talent_sets[best_idx] if build_talent_sets else set()
    missing = best_set - player_ids
    extra = player_ids - best_set

    # 解析天赋名称: 优先 nodeID（无歧义），退化到 entry ID
    def _resolve_talent(tid: int) -> str:
        # 对 extra（玩家独有），优先 nodeID 解析
        lookup = entry_to_node.get(tid, tid)
        zh = get_talent_name(lookup, lang="zh")
        en = get_talent_name(lookup, lang="en")
        if not zh and not en and lookup != tid:
            # nodeID 没找到，退化到 entry ID
            zh = get_talent_name(tid, lang="zh")
            en = get_talent_name(tid, lang="en")
        if zh and en and zh != en:
            return f"{zh} ({en})"
        return zh or en or f"TalentID {tid}"

    missing_names = [_resolve_talent(tid) for tid in sorted(missing)]
    extra_names = [_resolve_talent(tid) for tid in sorted(extra)]

    return BuildDivergence(
        best_match_build=best_idx + 1,
        similarity_pct=round(best_overlap * 100, 1),
        missing_meta_talents=missing_names,
        extra_talents=extra_names,
    )


# ============================================================
# 死亡分析
# ============================================================


def _analyze_deaths(
    death_events: list[dict],
    start_time: int,
) -> tuple[int, list[float]]:
    """
    分析玩家死亡事件。

    返回 (death_count, death_times_relative_seconds)
    """
    death_times: list[float] = []
    for evt in death_events:
        ts = evt.get("timestamp", 0)
        relative_sec = round((ts - start_time) / 1000.0, 1)
        death_times.append(relative_sec)
    return len(death_times), death_times


# ============================================================
# 归纳 Top Issues
# ============================================================


def _summarize_top_issues(
    rotation_gaps: list[SpellGap],
    cooldown_issues: list[CooldownIssue],
    defensive_issues: list[DefensiveIssue],
    build_div: BuildDivergence,
    player_deaths: int,
) -> list[str]:
    """
    从各维度差距中提炼 3-5 条最可操作的建议。

    优先级: 死亡 > 严重 undercast > 未使用防御 > 冷却缺失 > 天赋差异
    """
    issues: list[str] = []

    # 死亡
    if player_deaths > 0:
        issues.append(
            f"Died {player_deaths} time(s) during the fight — "
            f"review defensive timing."
        )

    # 严重 undercast 的技能（按差距排序）
    undercast = [
        g for g in rotation_gaps if g.verdict == "undercast"
    ]
    undercast.sort(
        key=lambda g: g.benchmark_median - g.player_casts, reverse=True
    )
    for g in undercast[:2]:
        diff = g.benchmark_median - g.player_casts
        issues.append(
            f"{g.name}: {g.player_casts} casts vs "
            f"{g.benchmark_median:.0f} benchmark median "
            f"({diff:.0f} fewer, below P25)."
        )

    # 未使用的防御技能
    unused_def = [d for d in defensive_issues if d.verdict == "unused"]
    for d in unused_def[:1]:
        issues.append(
            f"Defensive '{d.name}' not used "
            f"(top players use it {d.benchmark_usage_rate:.0f}% of fights)."
        )

    # 冷却缺失
    cd_sorted = sorted(
        cooldown_issues, key=lambda c: c.missed_uses, reverse=True
    )
    for c in cd_sorted[:1]:
        if c.missed_uses > 0:
            issues.append(
                f"Cooldown '{c.name}': {c.player_casts} uses vs "
                f"{c.benchmark_median_casts:.0f} benchmark — "
                f"~{c.missed_uses} missed uses."
            )

    # 天赋差异
    if build_div.missing_meta_talents:
        count = len(build_div.missing_meta_talents)
        issues.append(
            f"Build differs from meta (match {build_div.similarity_pct:.0f}%) "
            f"— missing {count} popular talent(s)."
        )

    return issues[:5]


# ============================================================
# 公开接口
# ============================================================


async def analyze_player_log(
    client: WCLClient,
    report: str,
    fight_id: int,
    player: str,
    spec: str,
    difficulty: str = "heroic",
) -> PlayerAnalysisResponse:
    """
    分析玩家在指定战斗中的表现，与基准数据对比。

    收集玩家施法、Buff、天赋、死亡数据，并行获取基准数据，
    产生循环差距、冷却问题、防御问题、天赋差异和 Top Issues。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        fight_id: 战斗 ID
        player: 角色名
        spec: 专精 slug，如 "balance-druid"
        difficulty: 难度 — "normal" / "heroic" / "mythic"

    Returns:
        PlayerAnalysisResponse 完整分析结果
    """
    difficulty = difficulty or "heroic"
    report_code = _extract_report_code(report)

    logger.info(
        "analyze_player_log: %s in %s fight=%d spec=%s",
        player, report_code, fight_id, spec,
    )

    # ---- Step 1: 收集玩家数据 ----
    player_data = await _collect_player_fight_data(
        client, report_code, fight_id, player,
    )

    fight_info = player_data["fight_info"]
    encounter_id = fight_info.get("encounterID", 0)
    encounter_name = fight_info.get("name", "")
    start_time = fight_info.get("startTime", 0)
    fight_duration = player_data["fight_duration"]

    # ---- Step 2: 并行获取基准数据 ----
    rotation_bench, timeline_bench, build_bench, defensive_bench = (
        await asyncio.gather(
            get_rotation_profile(
                client, spec, encounter_id, difficulty
            ),
            get_cooldown_timelines(
                client, spec=spec,
                encounter_id=encounter_id,
                difficulty=difficulty,
            ),
            get_top_builds(
                client, spec=spec,
                encounter_id=encounter_id,
                difficulty=difficulty,
            ),
            get_defensive_patterns(
                client, spec=spec,
                encounter_id=encounter_id,
                difficulty=difficulty,
            ),
            return_exceptions=True,
        )
    )

    # ---- Step 3: 对比分析 ----
    spell_counts = player_data["spell_counts"]
    spell_names = player_data["spell_names"]

    # 循环差距
    rotation_gaps: list[SpellGap] = []
    if not isinstance(rotation_bench, BaseException):
        rotation_gaps = _compare_rotation(
            spell_counts, spell_names, fight_duration, rotation_bench,
        )
    else:
        logger.warning("循环基准获取失败: %s", rotation_bench)

    # 冷却差距
    cooldown_issues: list[CooldownIssue] = []
    if not isinstance(timeline_bench, BaseException):
        cooldown_issues = _compare_cooldowns(
            spell_counts, spell_names, timeline_bench,
        )
    else:
        logger.warning("冷却基准获取失败: %s", timeline_bench)

    # 防御差距
    defensive_issues: list[DefensiveIssue] = []
    if not isinstance(defensive_bench, BaseException):
        defensive_issues = _compare_defensives(
            spell_counts, spell_names, defensive_bench,
        )
    else:
        logger.warning("防御基准获取失败: %s", defensive_bench)

    # 天赋差异
    build_divergence = BuildDivergence()
    if not isinstance(build_bench, BaseException):
        build_divergence = _compare_build(
            player_data["talents"], build_bench,
        )
    else:
        logger.warning("天赋基准获取失败: %s", build_bench)

    # 死亡分析
    player_deaths, death_times = _analyze_deaths(
        player_data["deaths"], start_time,
    )

    # ---- Step 4: 归纳 Top Issues ----
    top_issues = _summarize_top_issues(
        rotation_gaps,
        cooldown_issues,
        defensive_issues,
        build_divergence,
        player_deaths,
    )

    # ---- Step 5: DPS 百分位 ----
    player_dps = player_data.get("player_dps", 0.0)
    dps_percentile = ""
    if not isinstance(rotation_bench, BaseException) and player_dps > 0:
        if player_dps < rotation_bench.dps_p25:
            dps_percentile = "below_p25"
        elif player_dps < rotation_bench.dps_median:
            dps_percentile = "p25_p50"
        elif player_dps < rotation_bench.dps_p75:
            dps_percentile = "p50_p75"
        else:
            dps_percentile = "above_p75"

    # ---- Step 6: 解析玩家完整天赋列表 ----
    # 优先使用 nodeID (Blizzard ID)，因为 WCL entry ID 跨职业有歧义
    player_talent_names: list[str] = []
    for t in player_data["talents"]:
        # nodeID 是 Blizzard 节点 ID，在同一职业内唯一
        nid = t.get("nodeID")
        tid = t.get("id") or t.get("talentID")
        # 优先 nodeID 解析，退化到 entry ID
        lookup_id = nid or tid
        if lookup_id:
            zh = get_talent_name(lookup_id, lang="zh")
            en = get_talent_name(lookup_id, lang="en")
            if zh and en and zh != en:
                player_talent_names.append(f"{zh} ({en})")
            elif zh or en:
                player_talent_names.append(zh or en)

    # ---- Step 7: 构建响应 ----
    return PlayerAnalysisResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        difficulty=difficulty,
        player_dps=round(player_dps, 1),
        dps_percentile=dps_percentile,
        fight_duration=round(fight_duration, 1),
        player_deaths=player_deaths,
        death_times=death_times,
        rotation_gaps=rotation_gaps,
        cooldown_issues=cooldown_issues,
        defensive_issues=defensive_issues,
        player_talents=player_talent_names,
        build_divergence=build_divergence,
        top_issues=top_issues,
    )
