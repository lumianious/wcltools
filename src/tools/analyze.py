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
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.data import (
    get_spell_name,
    get_talent_name,
)
from src.models import (
    BuildDivergence,
    CDWindowThroughput,
    CooldownIssue,
    DefensiveIssue,
    PlayerAnalysisResponse,
    PlayerCombatStats,
    PlayerGearItem,
    PrepullBuff,
    SpellGap,
)
from src.tools._wcl_helpers import (
    extract_report_code,
    find_actor_id_ci,
    query_fight_info_full,
)
from src.tools._analysis_comparisons import (
    compare_rotation,
    compare_cooldowns,
    compare_defensives,
    compare_build,
    compare_talent_usage,
    compare_cd_throughput,
)
from src.tools._analysis_metrics import (
    analyze_deaths,
    analyze_downtime,
    analyze_cd_windows,
    analyze_eclipse_metrics,
    summarize_top_issues,
)
from src.tools.builds import get_top_builds
from src.tools.defensives import get_defensive_patterns
from src.tools.rotation import (
    _query_buff_table,
    _query_cast_events,
    _query_master_data,
    get_rotation_profile,
)
from src.tools.timelines import get_cooldown_timelines
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 向后兼容别名 — 测试和旧模块可能引用带下划线的旧名称
# ============================================================
_extract_report_code = extract_report_code
_find_actor_id_ci = find_actor_id_ci
_query_fight_info_full = query_fight_info_full
# 对比函数的向后兼容别名（测试文件中通过 from src.tools.analyze import 引用）
_compare_rotation = compare_rotation
_compare_cooldowns = compare_cooldowns
_compare_defensives = compare_defensives
_compare_build = compare_build
_compare_talent_usage = compare_talent_usage
_compare_cd_throughput = compare_cd_throughput
_analyze_deaths = analyze_deaths
_analyze_downtime = analyze_downtime
_analyze_cd_windows = analyze_cd_windows
_analyze_eclipse_metrics = analyze_eclipse_metrics
_summarize_top_issues = summarize_top_issues

# ============================================================
# 常量
# ============================================================
_INSTANT_GCD_MS = 1000  # 瞬发技能占用的 GCD 估算（毫秒）


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
    """查询玩家的 CombatantInfo 事件，提取天赋列表。"""
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
    """查询指定玩家的死亡事件。"""
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
        entries = table.get("data", {}).get("entries", [])
        return float(sum(e.get("total", 0) for e in entries))
    except Exception as exc:
        logger.warning("伤害总量查询失败 %s: %s", report_code, exc)
        return 0.0


# ============================================================
# 玩家数据收集
# ============================================================


async def _resolve_fight_and_player(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    player_name: str,
) -> tuple[dict, int, dict[int, str], int, int, float]:
    """解析战斗信息和玩家 ID，返回 (fight_info, source_id, ability_map, start, end, duration)。"""
    fight_info = await query_fight_info_full(client, report_code, fight_id)
    if not fight_info:
        raise ValueError(f"未找到战斗 fight_id={fight_id} in report {report_code}")

    start_time = fight_info.get("startTime", 0)
    end_time = fight_info.get("endTime", 0)
    if not start_time or not end_time:
        raise ValueError("战斗缺少有效的起止时间")

    actors, ability_map = await _query_master_data(client, report_code)
    source_id = find_actor_id_ci(actors, player_name)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player_name}' in report {report_code}")

    duration = (end_time - start_time) / 1000.0
    return fight_info, source_id, ability_map, start_time, end_time, duration


async def _collect_player_fight_data(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    player_name: str,
) -> dict[str, Any]:
    """收集玩家在指定战斗中的全部数据。"""
    fight_info, source_id, ability_map, start_time, end_time, fight_duration = (
        await _resolve_fight_and_player(client, report_code, fight_id, player_name)
    )

    # 并行查询施法/Buff/天赋/死亡/伤害
    events, auras, total_damage, combatant_events, death_events = (
        await asyncio.gather(
            _query_cast_events(client, report_code, start_time, end_time, source_id),
            _query_buff_table(client, report_code, start_time, end_time, source_id),
            _query_damage_done(client, report_code, start_time, end_time, source_id),
            _query_combatant_info(client, report_code, start_time, end_time, source_id),
            _query_death_events(client, report_code, start_time, end_time, source_id),
        )
    )

    spell_counts, spell_names, cast_timestamps, activity_intervals = (
        _process_cast_events(events, ability_map)
    )
    talents, gear_raw, auras_raw, combatant_raw = _extract_combatant_info(combatant_events)

    return {
        "fight_info": fight_info, "source_id": source_id,
        "ability_map": ability_map, "spell_counts": dict(spell_counts),
        "spell_names": spell_names, "buff_uptimes": auras,
        "talents": talents, "deaths": death_events,
        "fight_duration": fight_duration,
        "player_dps": total_damage / fight_duration if fight_duration > 0 else 0.0,
        "cast_timestamps": cast_timestamps, "activity_intervals": activity_intervals,
        "gear": gear_raw, "auras": auras_raw, "combatant_raw": combatant_raw,
    }


def _process_cast_events(
    events: list[dict],
    ability_map: dict[int, str],
) -> tuple[dict[int, int], dict[int, str], list[tuple[int, int]], list[tuple[int, int]]]:
    """处理施法事件，返回 (spell_counts, spell_names, cast_timestamps, activity_intervals)。"""
    spell_counts: dict[int, int] = defaultdict(int)
    spell_names: dict[int, str] = {}
    cast_timestamps: list[tuple[int, int]] = []
    activity_intervals: list[tuple[int, int]] = []
    pending_begincast: dict[int, int] = {}

    for evt in events:
        evt_type = evt.get("type")
        spell_id = evt.get("abilityGameID")
        ts = evt.get("timestamp")

        if evt_type == "begincast" and spell_id and ts is not None:
            pending_begincast[spell_id] = ts
        elif evt_type == "cast" and spell_id:
            spell_counts[spell_id] += 1
            if ts is not None:
                cast_timestamps.append((ts, spell_id))
                bc_ts = pending_begincast.pop(spell_id, None)
                if bc_ts is not None and bc_ts <= ts:
                    activity_intervals.append((bc_ts, ts))
                else:
                    activity_intervals.append((ts, ts + _INSTANT_GCD_MS))
            if spell_id not in spell_names:
                spell_names[spell_id] = (
                    ability_map.get(spell_id)
                    or get_spell_name(spell_id)
                    or f"Spell {spell_id}"
                )

    # 未配对的 begincast（取消施法）也算活动
    for _, bc_ts in pending_begincast.items():
        activity_intervals.append((bc_ts, bc_ts + _INSTANT_GCD_MS))

    return spell_counts, spell_names, cast_timestamps, activity_intervals


def _extract_combatant_info(
    combatant_events: list[dict],
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """提取 CombatantInfo 数据。"""
    if not combatant_events:
        return [], [], [], {}
    ci = combatant_events[0]
    talents = ci.get("talentTree", []) or ci.get("talents", [])
    gear_raw = ci.get("gear", [])
    auras_raw = ci.get("auras", [])
    return talents, gear_raw, auras_raw, ci


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
    report_code = extract_report_code(report)

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
    start_time = fight_info.get("startTime", 0)
    fight_duration = player_data["fight_duration"]

    # ---- Step 2: 并行获取基准数据 ----
    benchmarks = await _fetch_benchmarks(
        client, spec, encounter_id, difficulty,
    )

    # ---- Step 3: 对比分析 ----
    bench_results = _run_bench_comparisons(player_data, benchmarks, spec)
    metric_results = _run_metric_analyses(player_data, benchmarks, spec)
    analysis = {**bench_results, **metric_results}

    # ---- Step 4: 额外分析（CD 输出、APL、Eclipse） ----
    cd_throughput = await _run_cd_throughput_analysis(
        client, report_code, fight_id, player_data, analysis, benchmarks, start_time, fight_info,
    )
    apl_analysis = _run_apl_check(
        spec, player_data, start_time, fight_duration,
    )
    eclipse_metrics = None
    if spec == "balance-druid":
        eclipse_metrics = analyze_eclipse_metrics(
            player_data["buff_uptimes"], fight_duration,
        )

    # ---- Step 5: 归纳 Top Issues ----
    top_issues = summarize_top_issues(
        analysis["rotation_gaps"],
        analysis["cooldown_issues"],
        analysis["defensive_issues"],
        analysis["build_divergence"],
        analysis["player_deaths"],
        analysis["downtime"],
        analysis["cd_window_analysis"],
        analysis["talent_usage"],
        cd_throughput,
        apl_analysis,
    )

    # ---- Step 6: 构建响应 ----
    return _build_response(
        player_data, analysis, benchmarks, report_code, fight_id, player, spec,
        difficulty, cd_throughput, apl_analysis, eclipse_metrics, top_issues,
    )


async def _fetch_benchmarks(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str,
) -> dict[str, Any]:
    """并行获取全部基准数据。"""
    rotation_bench, timeline_bench, build_bench, defensive_bench = (
        await asyncio.gather(
            get_rotation_profile(client, spec, encounter_id, difficulty),
            get_cooldown_timelines(client, spec=spec, encounter_id=encounter_id, difficulty=difficulty),
            get_top_builds(client, spec=spec, encounter_id=encounter_id, difficulty=difficulty),
            get_defensive_patterns(client, spec=spec, encounter_id=encounter_id, difficulty=difficulty),
            return_exceptions=True,
        )
    )
    return {
        "rotation": rotation_bench,
        "timeline": timeline_bench,
        "build": build_bench,
        "defensive": defensive_bench,
    }


def _run_bench_comparisons(
    player_data: dict[str, Any],
    benchmarks: dict[str, Any],
    spec: str,
) -> dict[str, Any]:
    """执行基准对比分析（循环/冷却/防御/天赋构建/天赋技能使用）。"""
    sc = player_data["spell_counts"]
    sn = player_data["spell_names"]
    dur = player_data["fight_duration"]
    rb, tb, bb, db = (
        benchmarks["rotation"], benchmarks["timeline"],
        benchmarks["build"], benchmarks["defensive"],
    )

    rotation_gaps = (
        compare_rotation(sc, sn, dur, rb) if not isinstance(rb, BaseException) else []
    )
    cooldown_issues = (
        compare_cooldowns(sc, sn, tb, player_talents=player_data["talents"], spec=spec)
        if not isinstance(tb, BaseException) else []
    )
    defensive_issues = (
        compare_defensives(sc, sn, db) if not isinstance(db, BaseException) else []
    )
    build_divergence = (
        compare_build(player_data["talents"], bb, spec=spec)
        if not isinstance(bb, BaseException) else BuildDivergence()
    )
    talent_usage = (
        compare_talent_usage(player_data["talents"], sc, sn, dur, rb)
        if not isinstance(rb, BaseException) else None
    )

    return {
        "rotation_gaps": rotation_gaps,
        "cooldown_issues": cooldown_issues,
        "defensive_issues": defensive_issues,
        "build_divergence": build_divergence,
        "talent_usage": talent_usage,
    }


def _run_metric_analyses(
    player_data: dict[str, Any],
    benchmarks: dict[str, Any],
    spec: str,
) -> dict[str, Any]:
    """执行指标分析（死亡/停工/CD窗口事件关联）。"""
    start_time = player_data["fight_info"].get("startTime", 0)
    fight_duration = player_data["fight_duration"]
    rb = benchmarks["rotation"]

    player_deaths, death_times = analyze_deaths(player_data["deaths"], start_time)
    bench_for = rb if not isinstance(rb, BaseException) else None

    return {
        "player_deaths": player_deaths,
        "death_times": death_times,
        "downtime": analyze_downtime(
            player_data["activity_intervals"], fight_duration, start_time, bench_for,
        ),
        "cd_window_analysis": analyze_cd_windows(
            player_data["cast_timestamps"], player_data["buff_uptimes"],
            start_time, fight_duration, spec,
        ),
    }


async def _run_cd_throughput_analysis(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    player_data: dict[str, Any],
    analysis: dict[str, Any],
    benchmarks: dict[str, Any],
    start_time: int,
    fight_info: dict,
) -> list[CDWindowThroughput]:
    """运行 CD 窗口输出分析。"""
    cd_window_analysis = analysis["cd_window_analysis"]
    if not cd_window_analysis or not cd_window_analysis.cooldown_windows:
        return []

    rotation_bench = benchmarks["rotation"]
    bench_for = rotation_bench if not isinstance(rotation_bench, BaseException) else None

    try:
        from src.tools.timelines import _query_damage_events
        end_time = fight_info.get("endTime", 0)
        damage_events = await _query_damage_events(
            client, report_code, fight_id,
            player_data["source_id"],
            start_time=start_time, end_time=end_time,
        )
        return compare_cd_throughput(
            cd_window_analysis, damage_events, start_time, bench_for,
        )
    except Exception as exc:
        logger.warning("伤害事件查询失败: %s", exc)
        return []


def _run_apl_check(
    spec: str,
    player_data: dict[str, Any],
    start_time: int,
    fight_duration: float,
) -> Any:
    """运行 APL 循环检查。"""
    try:
        from src.apl_checker import check_player_apl
        return check_player_apl(
            spec=spec,
            cast_timestamps=player_data["cast_timestamps"],
            spell_names=player_data["spell_names"],
            buff_uptimes=player_data["buff_uptimes"],
            fight_start_time=start_time,
            fight_duration=fight_duration,
            talents=player_data["talents"],
        )
    except (ImportError, FileNotFoundError):
        return None
    except Exception as exc:
        logger.warning("APL 检查失败: %s", exc)
        return None


def _build_response(
    player_data: dict[str, Any],
    analysis: dict[str, Any],
    benchmarks: dict[str, Any],
    report_code: str,
    fight_id: int,
    player: str,
    spec: str,
    difficulty: str,
    cd_throughput: list[CDWindowThroughput],
    apl_analysis: Any,
    eclipse_metrics: Any,
    top_issues: list[str],
) -> PlayerAnalysisResponse:
    """构建完整的分析响应。"""
    fight_info = player_data["fight_info"]
    fight_duration = player_data["fight_duration"]
    player_dps = player_data.get("player_dps", 0.0)

    dps_percentile = _classify_dps(player_dps, benchmarks["rotation"])

    # 解析玩家天赋列表
    player_talent_names = _resolve_talent_list(player_data["talents"])

    # 装备
    player_gear = _parse_gear(player_data.get("gear", []))
    ilvls = [g.item_level for g in player_gear if g.item_level > 0]
    avg_ilvl = round(sum(ilvls) / len(ilvls), 1) if ilvls else 0.0

    # 开战 Buff
    prepull_buffs = _parse_prepull_buffs(player_data.get("auras", []))

    # 属性面板
    combat_stats = _parse_combat_stats(player_data.get("combatant_raw", {}))

    return PlayerAnalysisResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        spec=spec,
        encounter_id=fight_info.get("encounterID", 0),
        encounter_name=fight_info.get("name", ""),
        difficulty=difficulty,
        item_level=avg_ilvl,
        player_dps=round(player_dps, 1),
        dps_percentile=dps_percentile,
        fight_duration=round(fight_duration, 1),
        player_deaths=analysis["player_deaths"],
        death_times=analysis["death_times"],
        rotation_gaps=analysis["rotation_gaps"],
        cooldown_issues=analysis["cooldown_issues"],
        defensive_issues=analysis["defensive_issues"],
        player_gear=player_gear,
        prepull_buffs=prepull_buffs,
        combat_stats=combat_stats,
        player_talents=player_talent_names,
        build_divergence=analysis["build_divergence"],
        cd_window_analysis=analysis["cd_window_analysis"],
        talent_usage=analysis["talent_usage"],
        downtime=analysis["downtime"],
        cd_throughput=cd_throughput,
        apl_analysis=apl_analysis,
        eclipse_metrics=eclipse_metrics,
        top_issues=top_issues,
    )


def _classify_dps(player_dps: float, rotation_bench: Any) -> str:
    """根据 DPS 与基准对比确定百分位桶。"""
    if isinstance(rotation_bench, BaseException) or player_dps <= 0:
        return ""
    if player_dps < rotation_bench.dps_p25:
        return "below_p25"
    elif player_dps < rotation_bench.dps_median:
        return "p25_p50"
    elif player_dps < rotation_bench.dps_p75:
        return "p50_p75"
    return "above_p75"


def _resolve_talent_list(talents: list[dict]) -> list[str]:
    """解析玩家完整天赋列表。"""
    names: list[str] = []
    for t in talents:
        nid = t.get("nodeID")
        tid = t.get("id") or t.get("talentID")
        lookup_id = nid or tid
        if lookup_id:
            zh = get_talent_name(lookup_id, lang="zh")
            en = get_talent_name(lookup_id, lang="en")
            if zh and en and zh != en:
                names.append(f"{zh} ({en})")
            elif zh or en:
                names.append(zh or en)
    return names


def _parse_gear(gear_raw: list[dict]) -> list[PlayerGearItem]:
    """解析装备列表。"""
    gear: list[PlayerGearItem] = []
    for idx, item in enumerate(gear_raw):
        if not item or not item.get("id"):
            continue
        gear.append(PlayerGearItem(
            slot=idx,
            item_id=item.get("id", 0),
            name=item.get("name", f"Item {item.get('id', 0)}"),
            item_level=item.get("itemLevel", 0),
            quality=item.get("quality", 0),
        ))
    return gear


def _parse_prepull_buffs(auras_raw: list[dict]) -> list[PrepullBuff]:
    """解析开战 Buff。"""
    buffs: list[PrepullBuff] = []
    for aura in auras_raw:
        ability_id = aura.get("ability", 0)
        if ability_id:
            buffs.append(PrepullBuff(
                ability_id=ability_id,
                name=aura.get("name", f"Aura {ability_id}"),
                stacks=aura.get("stacks", 1),
            ))
    return buffs


def _parse_combat_stats(ci: dict) -> Optional[PlayerCombatStats]:
    """解析属性面板。"""
    if not ci:
        return None
    return PlayerCombatStats(
        stamina=ci.get("stamina", 0),
        intellect=ci.get("intellect", 0),
        strength=ci.get("strength", 0),
        agility=ci.get("agility", 0),
        crit=ci.get("critSpell", 0) or ci.get("critMelee", 0),
        haste=ci.get("hasteSpell", 0) or ci.get("hasteMelee", 0),
        mastery=ci.get("mastery", 0),
        versatility=ci.get("versatilityDamageDonePercent", 0),
        leech=ci.get("leech", 0),
        avoidance=ci.get("avoidance", 0),
        speed=ci.get("speed", 0),
    )
