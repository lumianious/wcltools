"""
analyze_dungeon_run 工具 — M+ 副本整体分析。

聚合一个 M+ 副本中所有战斗段落的伤害、死亡、Buff 覆盖率，
可选查询施法数据，产出副本级别的分析响应。

WCL 数据流:
  1. report.fights (无 fightIDs) -> 所有段落列表
  2. report.masterData -> actors + ability name map -> sourceID
  3. report.table(DamageDone, 全时段) -> 聚合伤害 + 技能排行
  4. report.table(Buffs, 全时段) -> Buff 覆盖率
  5. report.events(CombatantInfo) -> 天赋/装备快照
  6. report.events(Deaths) -> 死亡事件
  7. (可选) report.events(Casts) -> 施法事件（include_casts=True）
  8. (可选) per-segment DamageDone (<= 10 段时)

默认查询预算: ~5-7 WCL points（不含 per-segment 查询）
include_casts=True 时: +30-100 points（全施法分页）

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import logging
from typing import Any

# ============================================================
# 本地模块
# ============================================================
from src.data import get_spell_name, get_talent_name
from src.models import (
    DungeonRunAnalysisResponse,
    FightSegmentSummary,
)
from src.tools._wcl_helpers import extract_report_code, find_actor_id_ci
from src.tools.analyze import (
    _extract_combatant_info,
    _process_cast_events,
    _query_combatant_info,
    _query_damage_done,
    _query_death_events,
)
from src.tools.rotation import (
    _query_buff_table,
    _query_cast_events,
    _query_master_data,
)
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
_MAX_SEGMENTS_FOR_PER_FIGHT_DPS = 10  # 超过此数量不查段落 DPS
_TOP_N_ABILITIES = 15  # 伤害排行前 N


# ============================================================
# WCL 查询: 所有战斗段落
# ============================================================


async def _query_all_fights(
    client: WCLClient, report_code: str
) -> tuple[list[dict], str]:
    """
    查询报告中所有战斗段落（不传 fightIDs）。

    Returns:
        (fights_list, report_title)
    """
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
                }}
                title
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    fights = report.get("fights", [])
    title = report.get("title", "")
    return fights, title


# ============================================================
# 段落分类: boss vs trash
# ============================================================


def _classify_segments(
    fights: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    将战斗列表分为 boss 和 trash 段落，过滤掉 fight id 0（全局聚合）。

    Returns:
        (boss_fights, trash_fights)
    """
    bosses: list[dict] = []
    trash: list[dict] = []
    for f in fights:
        if f.get("id", 0) == 0:
            continue
        if f.get("encounterID", 0) > 0:
            bosses.append(f)
        else:
            trash.append(f)
    return bosses, trash


# ============================================================
# 伤害表查询（全时段聚合）
# ============================================================


async def _query_dungeon_damage_table(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> tuple[float, list[dict]]:
    """
    查询全时段伤害表，返回 (total_damage, ability_breakdown)。

    ability_breakdown 中每项: {name, total, pct}（前 15 名）
    """
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
        total = float(sum(e.get("total", 0) for e in entries))
        # 构建前 N 排行，附带百分比
        sorted_entries = sorted(entries, key=lambda e: e.get("total", 0), reverse=True)
        breakdown: list[dict] = []
        for e in sorted_entries[:_TOP_N_ABILITIES]:
            entry_total = e.get("total", 0)
            breakdown.append({
                "name": e.get("name", "Unknown"),
                "total": entry_total,
                "pct": round(entry_total / total * 100, 1) if total > 0 else 0.0,
            })
        return total, breakdown
    except Exception as exc:
        logger.warning("副本伤害表查询失败 %s: %s", report_code, exc)
        return 0.0, []


# ============================================================
# 编排器: analyze_dungeon_run
# ============================================================


async def analyze_dungeon_run(
    client: WCLClient,
    report: str,
    player: str,
    spec: str,
    include_casts: bool = False,
) -> DungeonRunAnalysisResponse:
    """
    分析玩家在整个 M+ 副本中的表现。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        player: 角色名（大小写不敏感）
        spec: 专精 slug，如 "frost-mage"
        include_casts: 是否查询完整施法数据（默认 False，节省 API 点数）

    Returns:
        DungeonRunAnalysisResponse 完整副本分析
    """
    report_code = extract_report_code(report)
    logger.info(
        "analyze_dungeon_run: %s in %s spec=%s include_casts=%s",
        player, report_code, spec, include_casts,
    )

    # ---- Step 1: 并行查询战斗列表 + masterData ----
    (fights, dungeon_title), (actors, ability_map) = await asyncio.gather(
        _query_all_fights(client, report_code),
        _query_master_data(client, report_code),
    )

    source_id = find_actor_id_ci(actors, player)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player}' in report {report_code}")

    # ---- Step 2: 分类段落，计算时间范围 ----
    real_fights = [f for f in fights if f.get("id", 0) != 0]
    if not real_fights:
        raise ValueError(f"报告 {report_code} 中无有效战斗段落")

    bosses, trash = _classify_segments(fights)

    first_start = min(f["startTime"] for f in real_fights)
    last_end = max(f["endTime"] for f in real_fights)
    # active_time = 各段时长之和（非 wall-clock）
    active_time_ms = sum(
        f["endTime"] - f["startTime"] for f in real_fights
    )
    active_time_sec = active_time_ms / 1000.0
    total_duration_sec = (last_end - first_start) / 1000.0

    # ---- Step 3: 并行查询 Tier 1+2 数据 ----
    damage_task = _query_dungeon_damage_table(
        client, report_code, first_start, last_end, source_id,
    )
    buff_task = _query_buff_table(
        client, report_code, first_start, last_end, source_id,
    )
    combatant_task = _query_combatant_info(
        client, report_code, first_start, last_end, source_id,
    )
    death_task = _query_death_events(
        client, report_code, first_start, last_end, source_id,
    )

    (total_damage, damage_breakdown), buff_auras, combatant_events, death_events = (
        await asyncio.gather(damage_task, buff_task, combatant_task, death_task)
    )

    # ---- Step 4: 处理死亡事件（按段落分配） ----
    total_deaths = len(death_events)
    death_times = [
        round((d.get("timestamp", 0) - first_start) / 1000.0, 1)
        for d in death_events
    ]

    # 按段落分配死亡数
    segment_deaths: dict[int, int] = {}
    for d in death_events:
        ts = d.get("timestamp", 0)
        for f in real_fights:
            if f["startTime"] <= ts <= f["endTime"]:
                fid = f["id"]
                segment_deaths[fid] = segment_deaths.get(fid, 0) + 1
                break

    # ---- Step 5: 可选 per-segment DPS 查询 ----
    segment_dps: dict[int, float] = {}
    if len(real_fights) <= _MAX_SEGMENTS_FOR_PER_FIGHT_DPS:
        seg_damage_tasks = [
            _query_damage_done(
                client, report_code, f["startTime"], f["endTime"], source_id,
            )
            for f in real_fights
        ]
        seg_damages = await asyncio.gather(*seg_damage_tasks)
        for f, dmg in zip(real_fights, seg_damages):
            dur_sec = (f["endTime"] - f["startTime"]) / 1000.0
            segment_dps[f["id"]] = round(dmg / dur_sec, 1) if dur_sec > 0 else 0.0

    # ---- Step 6: 构建 segments 列表 ----
    segments: list[FightSegmentSummary] = []
    for f in real_fights:
        dur_sec = (f["endTime"] - f["startTime"]) / 1000.0
        segments.append(FightSegmentSummary(
            fight_id=f["id"],
            name=f.get("name", "Unknown"),
            is_boss=f.get("encounterID", 0) > 0,
            duration_sec=round(dur_sec, 1),
            player_dps=segment_dps.get(f["id"], 0.0),
            deaths=segment_deaths.get(f["id"], 0),
        ))

    # ---- Step 7: 处理 Buff 覆盖率 ----
    buff_uptimes: list[dict] = []
    for aura in buff_auras:
        total_uptime = aura.get("totalUptime", 0)
        uptime_pct = round(total_uptime / active_time_ms * 100, 1) if active_time_ms > 0 else 0.0
        buff_uptimes.append({
            "name": aura.get("name", "Unknown"),
            "uptime_pct": uptime_pct,
        })

    # ---- Step 8: 处理 CombatantInfo ----
    talents_raw, gear_raw, _, _ = _extract_combatant_info(combatant_events)
    player_talents: list[str] = []
    for t in talents_raw:
        nid = t.get("nodeID")
        tid = t.get("id") or t.get("talentID")
        lookup_id = nid or tid
        if lookup_id:
            zh = get_talent_name(lookup_id, lang="zh")
            en = get_talent_name(lookup_id, lang="en")
            if zh and en and zh != en:
                player_talents.append(f"{zh} ({en})")
            elif zh or en:
                player_talents.append(zh or en)

    # 装等
    ilvls = [g.get("itemLevel", 0) for g in gear_raw if g.get("id")]
    avg_ilvl = round(sum(ilvls) / len(ilvls), 1) if ilvls else 0.0

    # ---- Step 9: 可选施法分析 ----
    spell_counts: dict[str, int] = {}
    active_time_pct = 0.0
    if include_casts:
        cast_events = await _query_cast_events(
            client, report_code, first_start, last_end, source_id,
        )
        counts_by_id, spell_names, _, activity_intervals = _process_cast_events(
            cast_events, ability_map,
        )
        # 转换为 spell_name -> count
        for sid, count in counts_by_id.items():
            name = spell_names.get(sid) or get_spell_name(sid) or f"Spell {sid}"
            spell_counts[name] = count

        # 计算活跃时间占比（合并重叠区间）
        if activity_intervals and active_time_sec > 0:
            sorted_intervals = sorted(activity_intervals)
            merged: list[tuple[int, int]] = [sorted_intervals[0]]
            for start, end in sorted_intervals[1:]:
                if start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            total_active_ms = sum(e - s for s, e in merged)
            active_time_pct = round(total_active_ms / active_time_ms * 100, 1) if active_time_ms > 0 else 0.0

    # ---- Step 10: DPS 计算 ----
    total_dps = round(total_damage / active_time_sec, 1) if active_time_sec > 0 else 0.0

    # ---- Step 11: top_issues 简单启发式 ----
    top_issues: list[str] = []
    if total_deaths >= 3:
        top_issues.append(f"死亡次数较多 ({total_deaths} 次)，注意生存意识")
    if active_time_pct > 0 and active_time_pct < 70:
        top_issues.append(f"活跃时间偏低 ({active_time_pct}%)，注意减少停工")

    # ---- Step 12: 构建响应 ----
    return DungeonRunAnalysisResponse(
        report_code=report_code,
        player_name=player,
        spec=spec,
        dungeon_name=dungeon_title,
        total_duration_sec=round(total_duration_sec, 1),
        active_time_sec=round(active_time_sec, 1),
        total_dps=total_dps,
        total_damage=total_damage,
        total_deaths=total_deaths,
        death_times=death_times,
        damage_by_ability=damage_breakdown,
        buff_uptimes=buff_uptimes,
        segments=segments,
        item_level=avg_ilvl,
        player_talents=player_talents,
        spell_counts=spell_counts,
        active_time_pct=active_time_pct,
        top_issues=top_issues,
    )
