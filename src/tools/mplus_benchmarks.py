"""
M+ Benchmark Aggregation — 从顶尖玩家报告中提取分段基准数据。

Pipeline: rankings -> reports -> segments -> extract(damage, CDs, interrupts) -> aggregate -> cache

WCL 数据流:
  1. query_mplus_rankings -> report_code + fight_id (from Phase 8)
  2. _query_all_fights -> fight list with gameZone/keystoneLevel
  3. _build_segment_positions -> boss-bounded segment alignment
  4. Per-segment queries: damage table, cast events, interrupt events
  5. Aggregate across 5 players with median

公开接口:
  - _build_segment_positions(fights, boss_names) -> list[dict]
  - _extract_segment_damage(entries, top_n) -> list[SegmentDamageBreakdown]
  - _extract_segment_cds(events, tracked_spells) -> (offensive, defensive)
  - _count_segment_interrupts(events) -> int
  - _compute_cd_spacing(segment_cds) -> list[dict]
  - _fetch_report_benchmark_data(client, entry, spec, boss_names) -> dict | None

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from collections import defaultdict

# ============================================================
# 本地模块
# ============================================================
from src.models import (
    MplusRankingEntry,
    SegmentCDCast,
    SegmentDamageBreakdown,
)
from src.tools._wcl_helpers import find_actor_id_ci
from src.tools.dungeon_analysis import _group_fights_by_dungeon, _query_all_fights
from src.tools.timelines import _build_tracked_spells, _query_master_data
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600
TOP_N_DAMAGE_SPELLS = 10


# ============================================================
# Section 2: Boss 边界段落对齐（纯函数）
# ============================================================


def _build_segment_positions(
    fights: list[dict], boss_names: list[str]
) -> list[dict]:
    """
    按 boss 边界分配段落位置。

    Position 方案: 0=第一段trash, 1=第一个boss, 2=boss间trash, 3=第二个boss, 依此类推。
    相邻 trash fight 合并为一个逻辑段落。

    Args:
        fights: 段落 fight 列表（不含聚合 fight）
        boss_names: 已知 boss 名称列表（用于名称匹配）

    Returns:
        [{position, segment_type, name, start_time, end_time, fights}, ...]
    """
    sorted_fights = sorted(fights, key=lambda f: f["startTime"])
    boss_set = {n.lower() for n in boss_names}
    segments: list[dict] = []
    position = 0

    # 累积中的 trash 段落
    trash_fights: list[dict] = []

    for f in sorted_fights:
        is_boss = f.get("name", "").lower() in boss_set

        if is_boss:
            # 先 flush 累积的 trash
            if trash_fights:
                segments.append(_flush_trash(trash_fights, position))
                position += 1
                trash_fights = []
            # 添加 boss 段落
            segments.append({
                "position": position,
                "segment_type": "boss",
                "name": f.get("name", ""),
                "start_time": f["startTime"],
                "end_time": f["endTime"],
                "fights": [f],
            })
            position += 1
        else:
            trash_fights.append(f)

    # 最后剩余的 trash
    if trash_fights:
        segments.append(_flush_trash(trash_fights, position))

    return segments


def _flush_trash(fights: list[dict], position: int) -> dict:
    """将累积的 trash fights 合并为一个逻辑段落。"""
    return {
        "position": position,
        "segment_type": "trash",
        "name": f"Trash #{position // 2 + 1}",
        "start_time": fights[0]["startTime"],
        "end_time": fights[-1]["endTime"],
        "fights": list(fights),
    }


# ============================================================
# Section 3: 段落伤害提取（纯函数）
# ============================================================


def _extract_segment_damage(
    entries: list[dict], top_n: int = TOP_N_DAMAGE_SPELLS
) -> list[SegmentDamageBreakdown]:
    """
    从 WCL damage table entries 提取 Top-N spell 伤害分布。

    Args:
        entries: WCL table.data.entries（含 name, total, id）
        top_n: 返回前 N 个技能

    Returns:
        按伤害降序的 SegmentDamageBreakdown 列表
    """
    total_damage = sum(e.get("total", 0) for e in entries)
    if total_damage == 0:
        return []

    sorted_entries = sorted(
        entries, key=lambda e: e.get("total", 0), reverse=True
    )
    return [
        SegmentDamageBreakdown(
            spell_name=e.get("name", ""),
            spell_id=e.get("id", 0),
            total_damage=e.get("total", 0),
            damage_pct=round(e.get("total", 0) / total_damage * 100, 1),
        )
        for e in sorted_entries[:top_n]
    ]


# ============================================================
# Section 4: 段落 CD 提取（纯函数）
# ============================================================


def _extract_segment_cds(
    events: list[dict], tracked_spells: dict[int, dict]
) -> tuple[list[SegmentCDCast], list[SegmentCDCast]]:
    """
    从施法事件中提取大 CD 施放，按进攻/防御分类。

    Args:
        events: WCL cast events 列表
        tracked_spells: {spell_id: {name, cd_seconds, ability_type}}

    Returns:
        (offensive, defensive) — 两个 SegmentCDCast 列表
    """
    # 统计每个 tracked spell 的施放次数
    counts: dict[int, int] = defaultdict(int)
    for ev in events:
        sid = ev.get("abilityGameID", 0)
        if sid in tracked_spells:
            counts[sid] += 1

    offensive: list[SegmentCDCast] = []
    defensive: list[SegmentCDCast] = []

    for sid, count in counts.items():
        info = tracked_spells[sid]
        cd = SegmentCDCast(
            spell_name=info["name"],
            spell_id=sid,
            cast_count_median=float(count),
            ability_type=info.get("ability_type", ""),
        )
        # dps/offensive/buff -> offensive 列表; defensive/raid_cd -> defensive 列表
        atype = info.get("ability_type", "")
        if atype in ("defensive", "raid_cd"):
            defensive.append(cd)
        else:
            offensive.append(cd)

    return offensive, defensive


# ============================================================
# Section 5: 打断统计（纯函数）
# ============================================================


def _count_segment_interrupts(events: list[dict]) -> int:
    """
    统计打断事件数量。

    Args:
        events: WCL dataType=Interrupts 事件列表

    Returns:
        成功打断次数
    """
    return len(events)


# ============================================================
# Section 6: CD 分布模式（纯函数）
# ============================================================


def _compute_cd_spacing(
    segment_cds: dict[int, list[SegmentCDCast]]
) -> list[dict]:
    """
    构建 CD 在各段落的分布图。

    Args:
        segment_cds: {position: [SegmentCDCast, ...]}

    Returns:
        [{spell_name, spell_id, ability_type, segments: [positions]}]
    """
    # 按 spell_id 聚合出现的段落位置
    spell_map: dict[int, dict] = {}
    for pos, cds in segment_cds.items():
        for cd in cds:
            if cd.spell_id not in spell_map:
                spell_map[cd.spell_id] = {
                    "spell_name": cd.spell_name,
                    "spell_id": cd.spell_id,
                    "ability_type": cd.ability_type,
                    "segments": [],
                }
            spell_map[cd.spell_id]["segments"].append(pos)

    # 段落位置排序
    result = list(spell_map.values())
    for item in result:
        item["segments"].sort()
    return result


# ============================================================
# Section 7: WCL 查询辅助函数（async）
# ============================================================


async def _query_segment_damage_table(
    client: WCLClient, report_code: str,
    start_time: int, end_time: int, source_id: int,
) -> list[dict]:
    """查询段落伤害表，返回 table.data.entries。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                table(startTime: {start_time}, endTime: {end_time},
                      sourceID: {source_id}, dataType: DamageDone)
            }}
        }}
    """
    data = await client.query(gql)
    table = data.get("reportData", {}).get("report", {}).get("table", {})
    return table.get("data", {}).get("entries", [])


async def _query_segment_events(
    client: WCLClient, report_code: str,
    start_time: int, end_time: int, source_id: int,
    data_type: str,
) -> list[dict]:
    """查询段落事件（Casts/Interrupts），处理分页。"""
    all_events: list[dict] = []
    next_ts: int | None = start_time
    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(startTime: {next_ts}, endTime: {end_time},
                           sourceID: {source_id}, dataType: {data_type},
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


async def _query_segment_cast_events(
    client: WCLClient, report_code: str,
    start_time: int, end_time: int, source_id: int,
) -> list[dict]:
    """查询段落施法事件。"""
    return await _query_segment_events(
        client, report_code, start_time, end_time, source_id, "Casts"
    )


async def _query_segment_interrupt_events(
    client: WCLClient, report_code: str,
    start_time: int, end_time: int, source_id: int,
) -> list[dict]:
    """查询段落打断事件。"""
    return await _query_segment_events(
        client, report_code, start_time, end_time, source_id, "Interrupts"
    )


# ============================================================
# Section 8: 单报告数据提取（async 编排）
# ============================================================


async def _fetch_report_benchmark_data(
    client: WCLClient, entry: MplusRankingEntry,
    spec: str, boss_names: list[str],
) -> dict | None:
    """从单个排行榜报告中提取全段落基准数据，失败返回 None。"""
    try:
        # Step 1: 获取 fights + 分组
        fights, _ = await _query_all_fights(client, entry.report_code)
        runs = _group_fights_by_dungeon(fights)

        # Step 2: 定位匹配 run
        run = _find_matching_run(runs, entry.fight_id)
        if run is None:
            logger.warning("未找到匹配 run: %s fight_id=%d", entry.report_code, entry.fight_id)
            return None

        # Step 3: masterData -> source_id
        actors = await _query_master_data(client, entry.report_code)
        source_id = find_actor_id_ci(actors, entry.name)
        if source_id is None:
            logger.warning("未找到玩家 %s in %s", entry.name, entry.report_code)
            return None

        # Step 4: 构建段落 + 查询各段数据
        seg_fights = [f for f in run.segment_fights if not _is_aggregate(f, run)]
        segments = _build_segment_positions(seg_fights, boss_names)
        tracked = _build_tracked_spells(spec)

        result_segments = await _extract_all_segments(
            client, entry.report_code, source_id, segments, tracked
        )

        return {"segments": result_segments, "source_id": source_id}

    except Exception as exc:
        logger.warning("报告数据提取失败 %s: %s", entry.report_code, exc)
        return None


def _find_matching_run(runs: list, fight_id: int):
    """在副本 run 列表中查找匹配 fight_id 的 run。"""
    # 优先: aggregate_fight.id 匹配
    for run in runs:
        if run.aggregate_fight and run.aggregate_fight.get("id") == fight_id:
            return run
    # 回退: 第一个有 segment_fights 的 run
    for run in runs:
        if run.segment_fights:
            return run
    return None


def _is_aggregate(fight: dict, run) -> bool:
    """判断 fight 是否为聚合 fight。"""
    agg = run.aggregate_fight
    return agg is not None and fight.get("id") == agg.get("id")


async def _extract_all_segments(
    client: WCLClient, report_code: str, source_id: int,
    segments: list[dict], tracked: dict[int, dict],
) -> list[dict]:
    """逐段提取 damage/casts/interrupts 数据。"""
    results: list[dict] = []
    for seg in segments:
        try:
            seg_data = await _extract_single_segment(
                client, report_code, source_id, seg, tracked
            )
            results.append(seg_data)
        except Exception as exc:
            logger.warning(
                "段落 %d 查询失败 (%s): %s", seg["position"], report_code, exc
            )
    return results


async def _extract_boss_benchmark(
    client: WCLClient,
    report_code: str,
    source_id: int,
    boss_fight: dict,
    tracked: dict[int, dict],
) -> dict:
    """
    提取 boss 段落的 cast-level 基准数据。

    Plan 03 实现完整管道集成，当前为占位函数。
    """
    raise NotImplementedError("Plan 03 实现")


async def _extract_single_segment(
    client: WCLClient, report_code: str, source_id: int,
    seg: dict, tracked: dict[int, dict],
) -> dict:
    """提取单个段落的 damage/CD/interrupt 数据。"""
    start = seg["start_time"]
    end = seg["end_time"]
    duration_sec = (end - start) / 1000.0

    # 查询三类数据
    damage_entries = await _query_segment_damage_table(
        client, report_code, start, end, source_id
    )
    cast_events = await _query_segment_cast_events(
        client, report_code, start, end, source_id
    )
    interrupt_events = await _query_segment_interrupt_events(
        client, report_code, start, end, source_id
    )

    # 提取结构化数据
    damage_breakdown = _extract_segment_damage(damage_entries)
    offensive, defensive = _extract_segment_cds(cast_events, tracked)
    interrupt_count = _count_segment_interrupts(interrupt_events)

    return {
        "position": seg["position"],
        "segment_type": seg["segment_type"],
        "name": seg["name"],
        "duration_sec": round(duration_sec, 1),
        "damage_breakdown": damage_breakdown,
        "cd_casts": offensive,
        "defensive_cds": defensive,
        "interrupt_count": interrupt_count,
    }
