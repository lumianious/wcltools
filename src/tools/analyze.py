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
from src.data import (
    get_class_spec_names,
    get_spec_spells,
    get_spell_name,
    get_talent_id_by_spell,
    get_talent_name,
    get_talent_spec,
    get_talent_spell_id,
)
from src.models import (
    BuildDivergence,
    CDWindowThroughput,
    CooldownIssue,
    CooldownWindowDetail,
    DefensiveIssue,
    DowntimeAnalysis,
    DowntimeWindow,
    EclipseMetrics,
    EventLinkingAnalysis,
    PlayerAnalysisResponse,
    PlayerCombatStats,
    PlayerGearItem,
    PrepullBuff,
    SpellGap,
    TalentUsageAnalysis,
    TalentUsageGap,
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

    # 统计施法次数 & 记录施法时间戳
    spell_counts: dict[int, int] = defaultdict(int)
    spell_names: dict[int, str] = {}
    cast_timestamps: list[tuple[int, int]] = []
    # 构建活动区间：begincast→cast 配对，用于 downtime 计算
    _pending_begincast: dict[int, int] = {}  # spell_id → begincast timestamp
    _INSTANT_GCD_MS = 1000  # 瞬发技能占用的 GCD 估算（毫秒）
    activity_intervals: list[tuple[int, int]] = []  # (start_ms, end_ms)
    for evt in events:
        evt_type = evt.get("type")
        spell_id = evt.get("abilityGameID")
        ts = evt.get("timestamp")
        if evt_type == "begincast" and spell_id and ts is not None:
            _pending_begincast[spell_id] = ts
        elif evt_type == "cast" and spell_id:
            spell_counts[spell_id] += 1
            if ts is not None:
                cast_timestamps.append((ts, spell_id))
                # 配对 begincast → cast 形成活动区间
                bc_ts = _pending_begincast.pop(spell_id, None)
                if bc_ts is not None and bc_ts <= ts:
                    activity_intervals.append((bc_ts, ts))
                else:
                    # 瞬发技能（无 begincast）→ 占用一个 GCD
                    activity_intervals.append((ts, ts + _INSTANT_GCD_MS))
            if spell_id not in spell_names:
                resolved = (
                    ability_map.get(spell_id)
                    or get_spell_name(spell_id)
                    or f"Spell {spell_id}"
                )
                spell_names[spell_id] = resolved
    # 未配对的 begincast（取消施法）也算活动
    for spell_id, bc_ts in _pending_begincast.items():
        activity_intervals.append((bc_ts, bc_ts + _INSTANT_GCD_MS))

    # 提取 CombatantInfo 数据（天赋、装备、buff、属性）
    talents: list[dict] = []
    gear_raw: list[dict] = []
    auras_raw: list[dict] = []
    combatant_raw: dict = {}
    if combatant_events:
        ci = combatant_events[0]
        combatant_raw = ci
        talents = ci.get("talentTree", []) or ci.get("talents", [])
        gear_raw = ci.get("gear", [])
        auras_raw = ci.get("auras", [])

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
        "cast_timestamps": cast_timestamps,
        "activity_intervals": activity_intervals,
        "gear": gear_raw,
        "auras": auras_raw,
        "combatant_raw": combatant_raw,
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
    player_talents: list[dict] | None = None,
    spec: str = "",
) -> list[CooldownIssue]:
    """
    将玩家冷却技能使用与基准时间线对比。

    基准来自 CooldownTimelineResponse.abilities。
    对于天赋授予的技能（如 Convoke / Incarnation 互斥选择行），
    若玩家未选择该天赋则跳过对比，避免误报。
    """
    issues: list[CooldownIssue] = []

    # 构建玩家天赋 spell_id 集合，用于判断天赋授予技能
    player_talent_spell_ids: set[int] = set()
    if player_talents:
        for t in player_talents:
            tid = t.get("id") or t.get("talentID")
            if tid:
                sid = get_talent_spell_id(tid)
                if sid:
                    player_talent_spell_ids.add(sid)

    # 构建 spec 的 CD 技能名称 → spell_id 映射
    spec_spell_by_name: dict[str, int] = {}
    if spec:
        for spell in get_spec_spells(spec):
            name = spell.get("name", "")
            sid = spell.get("spell_id", 0)
            if name and sid:
                spec_spell_by_name[name.lower()] = sid

    for ability in timeline_bench.abilities:
        # ability.total_casts 是 dict: {median, min, max}
        median_casts = ability.total_casts.get("median", 0.0)

        # 通过名称或 ability 中的信息匹配 spell_id
        # CooldownTimelineResponse 的 AbilityTimeline 没有 spell_id
        # 需要从 player_spell_names 中按名称反查
        matched_sid = _match_ability_spell_id(
            ability.name, player_spell_names
        )

        # 若未从玩家施法记录中匹配到，尝试从 spec 技能列表中查找
        if not matched_sid:
            matched_sid = spec_spell_by_name.get(ability.name.lower())

        # 检查该技能是否需要特定天赋，且玩家未选择该天赋
        if matched_sid:
            talent_entry_id = get_talent_id_by_spell(matched_sid)
            if talent_entry_id is not None and player_talent_spell_ids:
                # 天赋数据中有该技能 — 检查玩家是否拥有
                if matched_sid not in player_talent_spell_ids:
                    logger.debug(
                        "跳过天赋技能对比: %s (spell_id=%d) — 玩家未选择该天赋",
                        ability.name, matched_sid,
                    )
                    continue
            elif talent_entry_id is None:
                # 天赋数据中未找到 — 用施法记录判断
                # 如果玩家整场战斗从未施放过该技能，且该技能在 specs.json
                # 中标记为有 CD >= 30s（说明是主动技能），
                # 很可能是互斥天赋行的另一个选择
                if (matched_sid not in player_spell_counts
                        and matched_sid in spec_spell_by_name.values()):
                    # 额外确认: 检查玩家是否有该技能的名称变体
                    name_lower = ability.name.lower()
                    player_has_it = any(
                        name_lower in n.lower()
                        for n in player_spell_names.values()
                    )
                    if not player_has_it:
                        logger.debug(
                            "跳过未使用的专精技能: %s (spell_id=%d) — "
                            "玩家从未施放且不在施法记录中（可能为互斥天赋）",
                            ability.name, matched_sid,
                        )
                        continue

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
    spec: str = "",
) -> BuildDivergence:
    """
    将玩家天赋与热门构建对比。

    基准来自 TopBuildsResponse.builds。
    player_talents: [{id, ...}] — CombatantInfo 中的天赋列表。
    spec: 专精 slug，用于过滤跨职业天赋名称污染。
    """
    if not player_talents or not build_bench.builds:
        return BuildDivergence()

    # 获取当前职业所有专精的中文名称集合，用于过滤跨职业天赋
    valid_spec_names = get_class_spec_names(spec) if spec else set()

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

    def _is_same_class_talent(tid: int) -> bool:
        """检查天赋 ID 是否属于当前职业（过滤跨职业 ID 碰撞）。"""
        if not valid_spec_names:
            return True  # 无 spec 信息时不过滤
        talent_spec = get_talent_spec(tid)
        if talent_spec is None:
            return True  # 未找到天赋数据时保守保留
        return talent_spec in valid_spec_names

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

    # 过滤跨职业天赋后再解析名称
    missing_names = [
        _resolve_talent(tid)
        for tid in sorted(missing)
        if _is_same_class_talent(tid)
    ]
    extra_names = [
        _resolve_talent(tid)
        for tid in sorted(extra)
        if _is_same_class_talent(tid)
    ]

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
# 停工 / GCD 分析
# ============================================================

_DOWNTIME_GAP_THRESHOLD = 2.0  # 秒，合并后活动区间之间超过此值视为停工窗口


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠/相邻的活动区间。输入输出均为 (start_ms, end_ms) 列表。"""
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged: list[tuple[int, int]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _analyze_downtime(
    activity_intervals: list[tuple[int, int]],  # (start_ms, end_ms) 活动区间
    fight_duration: float,  # 秒
    fight_start_time: int,  # ms（WCL 绝对时间戳）
    rotation_bench: Any,  # RotationProfileResponse 或 None
) -> DowntimeAnalysis:
    """
    根据活动区间（begincast→cast 配对 + 瞬发 GCD）计算停工时间窗口。

    将所有活动区间合并后，检查区间之间的间隙。
    gap > 2.0 秒视为停工窗口，同时统计活跃时间百分比并与基准对比。
    """
    if fight_duration <= 0:
        return DowntimeAnalysis(
            active_time_pct=0.0,
            benchmark_active_time_pct=0.0,
            total_downtime_sec=0.0,
            verdict="ok",
        )

    fight_end_time = fight_start_time + int(fight_duration * 1000)

    downtime_windows: list[DowntimeWindow] = []
    total_downtime = 0.0

    merged = _merge_intervals(activity_intervals)

    if not merged:
        # 全程无施法 → 全部停工
        total_downtime = fight_duration
        downtime_windows.append(
            DowntimeWindow(
                start_sec=0.0,
                end_sec=round(fight_duration, 2),
                duration_sec=round(fight_duration, 2),
            )
        )
    else:
        # 战斗开始到第一个活动区间的间隔
        first_gap = (merged[0][0] - fight_start_time) / 1000.0
        if first_gap > _DOWNTIME_GAP_THRESHOLD:
            total_downtime += first_gap
            downtime_windows.append(
                DowntimeWindow(
                    start_sec=0.0,
                    end_sec=round(first_gap, 2),
                    duration_sec=round(first_gap, 2),
                )
            )

        # 合并后区间之间的间隔
        for i in range(1, len(merged)):
            gap = (merged[i][0] - merged[i - 1][1]) / 1000.0
            if gap > _DOWNTIME_GAP_THRESHOLD:
                start_sec = (merged[i - 1][1] - fight_start_time) / 1000.0
                end_sec = (merged[i][0] - fight_start_time) / 1000.0
                total_downtime += gap
                downtime_windows.append(
                    DowntimeWindow(
                        start_sec=round(start_sec, 2),
                        end_sec=round(end_sec, 2),
                        duration_sec=round(gap, 2),
                    )
                )

        # 最后一个活动区间到战斗结束的间隔
        last_gap = (fight_end_time - merged[-1][1]) / 1000.0
        if last_gap > _DOWNTIME_GAP_THRESHOLD:
            start_sec = (merged[-1][1] - fight_start_time) / 1000.0
            total_downtime += last_gap
            downtime_windows.append(
                DowntimeWindow(
                    start_sec=round(start_sec, 2),
                    end_sec=round(fight_duration, 2),
                    duration_sec=round(last_gap, 2),
                )
            )

    active_time_pct = round(
        (fight_duration - total_downtime) / fight_duration * 100, 1
    )

    # 基准活跃时间百分比
    # 使用有效 GCD 1.0 秒（考虑急速压缩 + 瞬发技能穿插），
    # 上限 95%（即使顶级玩家也不可能 100% 活跃）
    _EFFECTIVE_GCD = 1.0
    _MAX_BENCHMARK_ACTIVE_PCT = 95.0
    benchmark_active_time_pct = 0.0
    if rotation_bench is not None and hasattr(rotation_bench, "top_spells"):
        bench_duration = getattr(rotation_bench, "fight_duration_median", 0.0)
        if bench_duration > 0:
            total_bench_casts = sum(
                s.total_casts for s in rotation_bench.top_spells
            )
            benchmark_active_time_pct = round(
                min(
                    total_bench_casts * _EFFECTIVE_GCD / bench_duration * 100,
                    _MAX_BENCHMARK_ACTIVE_PCT,
                ),
                1,
            )

    # 判定
    if benchmark_active_time_pct > 0:
        diff = benchmark_active_time_pct - active_time_pct
        if diff > 15:
            verdict = "very_low_activity"
        elif diff > 5:
            verdict = "low_activity"
        else:
            verdict = "ok"
    else:
        # 无基准数据时仅凭绝对值判定
        if active_time_pct < 70:
            verdict = "very_low_activity"
        elif active_time_pct < 85:
            verdict = "low_activity"
        else:
            verdict = "ok"

    return DowntimeAnalysis(
        active_time_pct=active_time_pct,
        benchmark_active_time_pct=benchmark_active_time_pct,
        total_downtime_sec=round(total_downtime, 1),
        downtime_windows=downtime_windows,
        verdict=verdict,
    )


# ============================================================
# CD 窗口事件关联（Phase 6B）
# ============================================================

_CD_DENSITY_THRESHOLD = 0.70  # 密度低于 70% 视为低效窗口


def _analyze_cd_windows(
    cast_timestamps: list[tuple[int, int]],
    buff_uptimes: list[dict],
    fight_start_time: int,
    fight_duration: float,
    spec: str,
) -> EventLinkingAnalysis:
    """
    关联玩家施法与 CD Buff 窗口，分析每个窗口内的施法密度。

    通过 get_spec_spells 获取 CD >= 30s 且带 dps/raid_cd 标签的技能，
    在 buff_uptimes 中查找对应 aura 的 bands，统计窗口内施法数并计算密度。
    """
    if not cast_timestamps or not buff_uptimes or fight_duration <= 0:
        return EventLinkingAnalysis(verdict="ok")

    # 获取需要追踪的 CD 技能
    spells = get_spec_spells(spec)
    cd_spells: dict[int, str] = {}
    for s in spells:
        cd = s.get("cooldown", 0)
        tags = s.get("tags", [])
        if cd >= 30 and ("dps" in tags or "raid_cd" in tags):
            sid = s.get("spell_id")
            if sid:
                cd_spells[sid] = s.get("name", f"Spell {sid}")

    if not cd_spells:
        return EventLinkingAnalysis(verdict="ok")

    # 按时间排序施法时间戳
    sorted_casts = sorted(cast_timestamps, key=lambda x: x[0])

    windows: list[CooldownWindowDetail] = []
    low_density_count = 0

    for aura in buff_uptimes:
        aura_id = aura.get("id") or aura.get("guid")
        if aura_id not in cd_spells:
            continue

        buff_name = cd_spells[aura_id]
        bands = aura.get("bands", [])

        for band in bands:
            band_start = band.get("startTime", 0)
            band_end = band.get("endTime", 0)
            if band_start >= band_end:
                continue

            # 转为相对秒数
            start_sec = (band_start - fight_start_time) / 1000.0
            end_sec = (band_end - fight_start_time) / 1000.0
            duration_sec = end_sec - start_sec

            if duration_sec < 1.0:
                continue

            # 统计窗口内施法数
            casts_during = 0
            for ts, _ in sorted_casts:
                if ts < band_start:
                    continue
                if ts > band_end:
                    break
                casts_during += 1

            # 密度 = 实际施法 / 理论最大 GCD
            # 使用有效 GCD 1.0 秒（考虑急速压缩 + 瞬发/脱 GCD 技能）
            max_gcds = duration_sec / 1.0
            density = casts_during / max_gcds if max_gcds > 0 else 0.0

            if density < _CD_DENSITY_THRESHOLD:
                low_density_count += 1

            windows.append(CooldownWindowDetail(
                buff_name=buff_name,
                buff_spell_id=aura_id,
                start_sec=round(start_sec, 2),
                end_sec=round(end_sec, 2),
                duration_sec=round(duration_sec, 2),
                casts_during=casts_during,
                density_pct=round(density * 100, 1),
            ))

    verdict = "low_density_burst" if low_density_count > 0 else "ok"

    return EventLinkingAnalysis(
        cooldown_windows=windows,
        low_density_windows_count=low_density_count,
        verdict=verdict,
    )


# ============================================================
# 天赋技能使用分析（Phase 6C）
# ============================================================


def _compare_talent_usage(
    talents: list[dict],
    spell_counts: dict[int, int],
    spell_names: dict[int, str],
    fight_duration: float,
    rotation_bench: Any,
) -> TalentUsageAnalysis:
    """
    检查玩家天赋授予的技能是否被使用。

    将每个天赋解析为 spell_id，对比基准循环数据的施法次数。
    """
    dur_min = fight_duration / 60.0 if fight_duration > 0 else 1.0
    gaps: list[TalentUsageGap] = []
    unused_spells: list[str] = []

    # 构建基准查找表: spell_id -> SpellStats
    bench_by_id: dict[int, Any] = {}
    if rotation_bench is not None and hasattr(rotation_bench, "top_spells"):
        for ss in rotation_bench.top_spells:
            bench_by_id[ss.spell_id] = ss

    seen_spell_ids: set[int] = set()

    for t in talents:
        tid = t.get("id") or t.get("talentID")
        if not tid:
            continue

        spell_id = get_talent_spell_id(tid)
        if not spell_id or spell_id in seen_spell_ids:
            continue
        seen_spell_ids.add(spell_id)

        # 获取天赋名称
        talent_zh = get_talent_name(tid, lang="zh")
        talent_en = get_talent_name(tid, lang="en")
        if talent_zh and talent_en and talent_zh != talent_en:
            talent_name = f"{talent_zh} ({talent_en})"
        else:
            talent_name = talent_zh or talent_en or f"TalentID {tid}"

        # 获取技能名称
        spell_name = spell_names.get(spell_id) or get_spell_name(spell_id) or f"Spell {spell_id}"

        # 玩家施法数
        player_casts = spell_counts.get(spell_id, 0)
        player_cpm = round(player_casts / dur_min, 2)

        # 基准数据
        bench = bench_by_id.get(spell_id)
        bench_median = bench.total_casts if bench else 0.0
        bench_cpm = bench.cpm if bench else 0.0

        # 判定
        if player_casts == 0 and bench_median > 0:
            verdict = "unused"
            unused_spells.append(spell_name)
        elif bench_median > 0 and player_casts < bench_median * 0.5:
            verdict = "underused"
        else:
            verdict = "ok"

        # 只记录有基准数据或有问题的天赋
        if bench_median > 0 or verdict != "ok":
            gaps.append(TalentUsageGap(
                talent_name=talent_name,
                talent_id=tid,
                spell_name=spell_name,
                spell_id=spell_id,
                player_casts=player_casts,
                benchmark_median_casts=bench_median,
                player_cpm=player_cpm,
                benchmark_cpm=bench_cpm,
                verdict=verdict,
            ))

    return TalentUsageAnalysis(
        talent_gaps=gaps,
        unused_talent_spells=unused_spells,
    )


# ============================================================
# CD 窗口输出分析（Phase 6D）
# ============================================================


def _compare_cd_throughput(
    cd_window_analysis: Optional[EventLinkingAnalysis],
    damage_events: list[dict],
    fight_start_time: int,
    rotation_bench: Any,
) -> list[CDWindowThroughput]:
    """
    分析每个 CD 窗口期间的伤害输出。

    用 rotation_bench 的 DPS 中位数 × 窗口时长作为基准伤害量。
    """
    if not cd_window_analysis or not cd_window_analysis.cooldown_windows:
        return []

    # 基准 DPS（用于计算窗口内预期伤害量）
    bench_dps = 0.0
    if rotation_bench is not None and hasattr(rotation_bench, "dps_median"):
        bench_dps = rotation_bench.dps_median

    results: list[CDWindowThroughput] = []

    # 按技能名称分组窗口以计算 window_index
    name_counters: dict[str, int] = defaultdict(int)

    for window in cd_window_analysis.cooldown_windows:
        name_counters[window.buff_name] += 1
        window_index = name_counters[window.buff_name]

        # 计算窗口时间范围（转回绝对时间戳用于匹配伤害事件）
        window_start_ms = fight_start_time + int(window.start_sec * 1000)
        window_end_ms = fight_start_time + int(window.end_sec * 1000)

        # 统计窗口内伤害
        damage_done = 0.0
        cast_count = 0
        for evt in damage_events:
            ts = evt.get("timestamp", 0)
            if ts < window_start_ms:
                continue
            if ts > window_end_ms:
                continue
            damage_done += evt.get("amount", 0) + evt.get("absorbed", 0)
            cast_count += 1

        # 基准伤害 = 基准 DPS × 窗口时长
        benchmark_damage = bench_dps * window.duration_sec if bench_dps > 0 else 0.0

        # 活跃时间 = 密度（已在 6B 中计算）
        active_time_pct = window.density_pct

        # 判定
        if benchmark_damage > 0:
            ratio = damage_done / benchmark_damage
            if ratio >= 1.0:
                verdict = "strong"
            elif ratio >= 0.5:
                verdict = "average"
            else:
                verdict = "weak"
        else:
            verdict = "ok"

        results.append(CDWindowThroughput(
            ability_name=window.buff_name,
            window_index=window_index,
            damage_done=round(damage_done, 1),
            casts_during=window.casts_during,
            active_time_pct=active_time_pct,
            benchmark_median_damage=round(benchmark_damage, 1),
            verdict=verdict,
        ))

    return results


# ============================================================
# 归纳 Top Issues
# ============================================================


def _summarize_top_issues(
    rotation_gaps: list[SpellGap],
    cooldown_issues: list[CooldownIssue],
    defensive_issues: list[DefensiveIssue],
    build_div: BuildDivergence,
    player_deaths: int,
    downtime: Optional[DowntimeAnalysis] = None,
    cd_window_analysis: Optional[EventLinkingAnalysis] = None,
    talent_usage: Optional[TalentUsageAnalysis] = None,
    cd_throughput: Optional[list[CDWindowThroughput]] = None,
    apl_analysis: Any = None,
) -> list[str]:
    """
    从各维度差距中提炼 3-5 条最可操作的建议。

    优先级: 死亡 > 停工 > CD 窗口低密度 > 未使用天赋技能
           > 严重 undercast > 未使用防御 > 冷却缺失 > CD 窗口低输出
           > APL 合规 > 天赋差异
    """
    issues: list[str] = []

    # 死亡
    if player_deaths > 0:
        issues.append(
            f"Died {player_deaths} time(s) during the fight — "
            f"review defensive timing."
        )

    # 停工
    if downtime and downtime.verdict in ("low_activity", "very_low_activity"):
        gap = round(downtime.benchmark_active_time_pct - downtime.active_time_pct, 1)
        severity = "significantly " if downtime.verdict == "very_low_activity" else ""
        issues.append(
            f"Active time {severity}below benchmark: "
            f"{downtime.active_time_pct:.1f}% vs "
            f"{downtime.benchmark_active_time_pct:.1f}% "
            f"({gap:.1f}% gap, {downtime.total_downtime_sec:.1f}s total downtime)."
        )

    # CD 窗口低密度（Phase 6B）
    if cd_window_analysis and cd_window_analysis.verdict == "low_density_burst":
        low_windows = [
            w for w in cd_window_analysis.cooldown_windows
            if w.density_pct < _CD_DENSITY_THRESHOLD * 100
        ]
        if low_windows:
            w = low_windows[0]
            issues.append(
                f"Low GCD density during '{w.buff_name}' window "
                f"({w.density_pct:.0f}% of max GCDs) — "
                f"fill every GCD during cooldown windows."
            )

    # 未使用天赋技能（Phase 6C）
    if talent_usage and talent_usage.unused_talent_spells:
        spells = talent_usage.unused_talent_spells[:2]
        issues.append(
            f"Talent spell(s) never cast: {', '.join(spells)} — "
            f"these abilities are available but unused."
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

    # CD 窗口低输出（Phase 6D）
    if cd_throughput:
        weak_windows = [t for t in cd_throughput if t.verdict == "weak"]
        if weak_windows:
            w = weak_windows[0]
            issues.append(
                f"Weak damage during '{w.ability_name}' window #{w.window_index} — "
                f"only {w.damage_done:.0f} damage vs {w.benchmark_median_damage:.0f} expected."
            )

    # APL 合规（Phase 6E）
    if apl_analysis is not None and hasattr(apl_analysis, "compliance_pct"):
        if apl_analysis.compliance_pct < 70:
            issues.append(
                f"APL compliance low ({apl_analysis.compliance_pct:.0f}%) — "
                f"{apl_analysis.high_severity_count} high-severity violations."
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
# Phase 7: Eclipse 指标分析（Balance Druid 专用）
# ============================================================

# 已知 Eclipse 相关 Buff 名称关键词
_ECLIPSE_BUFF_KEYWORDS = ["eclipse"]
_STARLORD_BUFF_KEYWORDS = ["starlord"]
_CA_BUFF_KEYWORDS = ["celestial alignment", "incarnation"]


def _analyze_eclipse_metrics(
    buff_uptimes: list[dict],
    fight_duration: float,
) -> Optional[EclipseMetrics]:
    """
    从 Buff 覆盖率数据中提取 Eclipse 相关指标。

    仅适用于 Balance Druid。从 WCL buff table (auras) 中提取:
    - Eclipse 总覆盖率
    - Starlord 覆盖率
    - CA/Incarnation 期间 Eclipse 覆盖率（简化为 CA 覆盖率本身）

    Args:
        buff_uptimes: WCL buff table auras 列表
        fight_duration: 战斗时长（秒）

    Returns:
        EclipseMetrics 或 None（无 Eclipse 数据时）
    """
    if fight_duration <= 0 or not buff_uptimes:
        return None

    fight_dur_ms = fight_duration * 1000.0

    eclipse_uptime_ms = 0.0
    starlord_uptime_ms = 0.0
    ca_uptime_ms = 0.0
    found_eclipse = False

    for aura in buff_uptimes:
        name = (aura.get("name") or "").lower()
        total_uptime = aura.get("totalUptime", 0)

        # Eclipse (Solar/Lunar)
        if any(kw in name for kw in _ECLIPSE_BUFF_KEYWORDS):
            eclipse_uptime_ms += total_uptime
            found_eclipse = True

        # Starlord
        if any(kw in name for kw in _STARLORD_BUFF_KEYWORDS):
            starlord_uptime_ms += total_uptime

        # CA / Incarnation
        if any(kw in name for kw in _CA_BUFF_KEYWORDS):
            ca_uptime_ms += total_uptime

    if not found_eclipse:
        return None

    eclipse_uptime_pct = round(min((eclipse_uptime_ms / fight_dur_ms) * 100.0, 100.0), 1)
    starlord_uptime_pct = round(min((starlord_uptime_ms / fight_dur_ms) * 100.0, 100.0), 1)
    # 简化: CA Eclipse 覆盖率 ≈ CA 覆盖率（CA 期间几乎总是在 Eclipse 中）
    ca_eclipse_coverage_pct = round(min((ca_uptime_ms / fight_dur_ms) * 100.0, 100.0), 1)

    return EclipseMetrics(
        eclipse_uptime_pct=eclipse_uptime_pct,
        avg_eclipse_gap_sec=0.0,  # v1: 需要逐事件追踪，暂不实现
        ca_eclipse_coverage_pct=ca_eclipse_coverage_pct,
        starlord_uptime_pct=starlord_uptime_pct,
    )


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
            player_talents=player_data["talents"],
            spec=spec,
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
            player_data["talents"], build_bench, spec=spec,
        )
    else:
        logger.warning("天赋基准获取失败: %s", build_bench)

    # 死亡分析
    player_deaths, death_times = _analyze_deaths(
        player_data["deaths"], start_time,
    )

    # 停工分析
    bench_for_downtime = (
        rotation_bench
        if not isinstance(rotation_bench, BaseException)
        else None
    )
    downtime_analysis = _analyze_downtime(
        player_data["activity_intervals"],
        fight_duration,
        start_time,
        bench_for_downtime,
    )

    # CD 窗口事件关联（Phase 6B）
    cd_window_analysis = _analyze_cd_windows(
        player_data["cast_timestamps"],
        player_data["buff_uptimes"],
        start_time,
        fight_duration,
        spec,
    )

    # 天赋技能使用分析（Phase 6C）
    talent_usage = None
    if not isinstance(rotation_bench, BaseException):
        talent_usage = _compare_talent_usage(
            player_data["talents"],
            spell_counts,
            spell_names,
            fight_duration,
            rotation_bench,
        )

    # CD 窗口输出分析（Phase 6D）
    cd_throughput: list[CDWindowThroughput] = []
    if cd_window_analysis.cooldown_windows:
        # 查询玩家伤害事件
        from src.tools.timelines import _query_damage_events
        try:
            end_time = fight_info.get("endTime", 0)
            damage_events = await _query_damage_events(
                client, report_code, fight_id,
                player_data["source_id"],
                start_time=start_time,
                end_time=end_time,
            )
            cd_throughput = _compare_cd_throughput(
                cd_window_analysis,
                damage_events,
                start_time,
                bench_for_downtime,
            )
        except Exception as exc:
            logger.warning("伤害事件查询失败: %s", exc)

    # APL 循环检查（Phase 6E）
    apl_analysis = None
    try:
        from src.apl_checker import check_player_apl
        apl_analysis = check_player_apl(
            spec=spec,
            cast_timestamps=player_data["cast_timestamps"],
            spell_names=spell_names,
            buff_uptimes=player_data["buff_uptimes"],
            fight_start_time=start_time,
            fight_duration=fight_duration,
            talents=player_data["talents"],
        )
    except (ImportError, FileNotFoundError):
        pass  # APL 数据不可用时跳过
    except Exception as exc:
        logger.warning("APL 检查失败: %s", exc)

    # Eclipse 指标（Phase 7 — Balance Druid 专用）
    eclipse_metrics = None
    if spec == "balance-druid":
        eclipse_metrics = _analyze_eclipse_metrics(
            player_data["buff_uptimes"],
            fight_duration,
        )

    # ---- Step 4: 归纳 Top Issues ----
    top_issues = _summarize_top_issues(
        rotation_gaps,
        cooldown_issues,
        defensive_issues,
        build_divergence,
        player_deaths,
        downtime_analysis,
        cd_window_analysis,
        talent_usage,
        cd_throughput,
        apl_analysis,
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

    # ---- Step 7: 解析装备、消耗品、属性 ----
    player_gear: list[PlayerGearItem] = []
    for idx, item in enumerate(player_data.get("gear", [])):
        if not item or not item.get("id"):
            continue
        player_gear.append(PlayerGearItem(
            slot=idx,
            item_id=item.get("id", 0),
            name=item.get("name", f"Item {item.get('id', 0)}"),
            item_level=item.get("itemLevel", 0),
            quality=item.get("quality", 0),
        ))

    # 计算平均装等
    ilvls = [g.item_level for g in player_gear if g.item_level > 0]
    avg_ilvl = round(sum(ilvls) / len(ilvls), 1) if ilvls else 0.0

    # 开战 buff（精炼药剂、食物、增强符文等）
    prepull_buffs: list[PrepullBuff] = []
    for aura in player_data.get("auras", []):
        ability_id = aura.get("ability", 0)
        if ability_id:
            prepull_buffs.append(PrepullBuff(
                ability_id=ability_id,
                name=aura.get("name", f"Aura {ability_id}"),
                stacks=aura.get("stacks", 1),
            ))

    # 属性面板
    ci = player_data.get("combatant_raw", {})
    combat_stats = None
    if ci:
        combat_stats = PlayerCombatStats(
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

    # ---- Step 8: 构建响应 ----
    return PlayerAnalysisResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        difficulty=difficulty,
        item_level=avg_ilvl,
        player_dps=round(player_dps, 1),
        dps_percentile=dps_percentile,
        fight_duration=round(fight_duration, 1),
        player_deaths=player_deaths,
        death_times=death_times,
        rotation_gaps=rotation_gaps,
        cooldown_issues=cooldown_issues,
        defensive_issues=defensive_issues,
        player_gear=player_gear,
        prepull_buffs=prepull_buffs,
        combat_stats=combat_stats,
        player_talents=player_talent_names,
        build_divergence=build_divergence,
        cd_window_analysis=cd_window_analysis,
        talent_usage=talent_usage,
        downtime=downtime_analysis,
        cd_throughput=cd_throughput,
        apl_analysis=apl_analysis,
        eclipse_metrics=eclipse_metrics,
        top_issues=top_issues,
    )
