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
  - get_mplus_benchmarks(client, spec, encounter_id, key_level) -> MplusBenchmarkResponse
  - _build_segment_positions(fights, boss_names) -> list[dict]
  - _extract_segment_damage(entries, top_n) -> list[SegmentDamageBreakdown]
  - _extract_segment_cds(events, tracked_spells) -> (offensive, defensive)
  - _count_segment_interrupts(events) -> int
  - _compute_cd_spacing(segment_cds) -> list[dict]
  - _aggregate_segment_data(all_reports) -> list[MplusBenchmarkSegment]
  - _fetch_report_benchmark_data(client, entry, spec, boss_names) -> dict | None

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import logging
import statistics
from collections import defaultdict

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.models import (
    MplusBenchmarkMeta,
    MplusBenchmarkResponse,
    MplusBenchmarkSegment,
    MplusRankingEntry,
    SegmentCDCast,
    SegmentDamageBreakdown,
)
from src.tools._wcl_helpers import find_actor_id_ci
from src.tools.dungeon_analysis import _group_fights_by_dungeon, _query_all_fights
from src.tools.mplus_rankings import query_mplus_rankings
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

    查询 boss 战斗时间范围内的施法事件，统计每个技能的施放次数和 CPM。
    """
    start = boss_fight["startTime"]
    end = boss_fight["endTime"]
    duration_sec = (end - start) / 1000.0

    cast_events = await _query_segment_cast_events(
        client, report_code, start, end, source_id
    )

    # 统计每个技能的施放次数
    spell_counts: dict[int, dict] = {}
    for ev in cast_events:
        sid = ev.get("abilityGameID", 0)
        name = ev.get("ability", {}).get("name", "") if isinstance(ev.get("ability"), dict) else ""
        if not name:
            # 从 tracked spells 获取名称
            info = tracked.get(sid)
            name = info["name"] if info else f"Spell#{sid}"
        if sid not in spell_counts:
            spell_counts[sid] = {"spell_name": name, "cast_count": 0}
        spell_counts[sid]["cast_count"] += 1

    # 构建 cast_stats
    cast_stats = []
    for sid, info in sorted(
        spell_counts.items(), key=lambda x: x[1]["cast_count"], reverse=True
    ):
        cpm = round(info["cast_count"] / max(duration_sec / 60, 0.01), 1)
        cast_stats.append({
            "spell_name": info["spell_name"],
            "spell_id": sid,
            "cast_count": info["cast_count"],
            "cpm": cpm,
        })

    return {
        "boss_name": boss_fight.get("name", ""),
        "position": boss_fight.get("position", 0),
        "duration_sec": round(duration_sec, 1),
        "cast_stats": cast_stats[:TOP_N_DAMAGE_SPELLS],
    }


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


# ============================================================
# Section 9: 跨玩家中位数聚合（纯函数）
# ============================================================


def _aggregate_segment_data(
    all_reports: list[dict],
) -> list[MplusBenchmarkSegment]:
    """
    跨多个报告聚合段落数据，使用中位数。

    按 position 分组段落，对每个 position 的 duration、damage_pct、
    cast_count、interrupt_count 取中位数。需要 >= 2 份报告才计算中位数。

    Args:
        all_reports: _fetch_report_benchmark_data 返回的报告列表

    Returns:
        按 position 排序的 MplusBenchmarkSegment 列表
    """
    # 按 position 分组
    pos_map: dict[int, list[dict]] = defaultdict(list)
    for report in all_reports:
        for seg in report.get("segments", []):
            pos_map[seg["position"]].append(seg)

    results: list[MplusBenchmarkSegment] = []
    for pos in sorted(pos_map.keys()):
        segs = pos_map[pos]
        if len(segs) < 1:
            continue

        # 基本信息从第一个报告取（所有报告共享 boss 结构）
        first = segs[0]

        # duration 中位数
        duration_median = round(
            statistics.median([s["duration_sec"] for s in segs]), 1
        )

        # 伤害分布聚合: 按 spell_id 分组，取 damage_pct 中位数
        damage_breakdown = _aggregate_damage_breakdown(segs)

        # CD 聚合: 按 spell_id 分组，取 cast_count 中位数
        cd_casts = _aggregate_cd_casts(segs, "cd_casts")
        defensive_cds = _aggregate_cd_casts(segs, "defensive_cds")

        # 打断中位数
        interrupt_median = round(
            statistics.median([s["interrupt_count"] for s in segs]), 1
        )

        results.append(MplusBenchmarkSegment(
            position=pos,
            segment_type=first["segment_type"],
            segment_name=first["name"],
            duration_median=duration_median,
            damage_breakdown=damage_breakdown,
            cd_casts=cd_casts,
            defensive_cds=defensive_cds,
            interrupt_count_median=interrupt_median,
        ))

    return results


def _aggregate_damage_breakdown(
    segs: list[dict],
) -> list[SegmentDamageBreakdown]:
    """跨报告聚合伤害分布，按 spell_id 分组取 damage_pct 中位数。"""
    spell_data: dict[int, dict] = {}
    for seg in segs:
        for dmg in seg.get("damage_breakdown", []):
            # 支持 dict 或 SegmentDamageBreakdown 对象
            if isinstance(dmg, dict):
                sid = dmg.get("spell_id", 0)
                name = dmg.get("spell_name", "")
                pct = dmg.get("damage_pct", 0.0)
                total = dmg.get("total_damage", 0.0)
            else:
                sid = dmg.spell_id
                name = dmg.spell_name
                pct = dmg.damage_pct
                total = dmg.total_damage

            if sid not in spell_data:
                spell_data[sid] = {
                    "spell_name": name, "pcts": [], "totals": [],
                }
            spell_data[sid]["pcts"].append(pct)
            spell_data[sid]["totals"].append(total)

    # 按中位数 damage_pct 降序取 TOP_N
    result = []
    for sid, info in spell_data.items():
        median_pct = round(statistics.median(info["pcts"]), 1)
        median_total = round(statistics.median(info["totals"]), 0)
        result.append(SegmentDamageBreakdown(
            spell_name=info["spell_name"],
            spell_id=sid,
            total_damage=median_total,
            damage_pct=median_pct,
        ))

    result.sort(key=lambda x: x.damage_pct, reverse=True)
    return result[:TOP_N_DAMAGE_SPELLS]


def _aggregate_cd_casts(
    segs: list[dict], key: str,
) -> list[SegmentCDCast]:
    """跨报告聚合 CD 施放，按 spell_id 分组取 cast_count 中位数。"""
    spell_data: dict[int, dict] = {}
    for seg in segs:
        for cd in seg.get(key, []):
            # 支持 dict 或 SegmentCDCast 对象
            if isinstance(cd, dict):
                sid = cd.get("spell_id", 0)
                name = cd.get("spell_name", "")
                count = cd.get("cast_count_median", 0.0)
                atype = cd.get("ability_type", "")
            else:
                sid = cd.spell_id
                name = cd.spell_name
                count = cd.cast_count_median
                atype = cd.ability_type

            if sid not in spell_data:
                spell_data[sid] = {
                    "spell_name": name,
                    "ability_type": atype,
                    "counts": [],
                }
            spell_data[sid]["counts"].append(count)

    result = []
    for sid, info in spell_data.items():
        median_count = round(statistics.median(info["counts"]), 1)
        result.append(SegmentCDCast(
            spell_name=info["spell_name"],
            spell_id=sid,
            cast_count_median=median_count,
            ability_type=info["ability_type"],
        ))
    return result


# ============================================================
# Section 10: 公开管道函数（async 入口）
# ============================================================


async def get_mplus_benchmarks(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    key_level: int,
) -> MplusBenchmarkResponse:
    """
    获取 M+ 副本基准数据（公开接口）。

    从 WCL 排行榜获取顶尖玩家报告，提取分段基准数据并聚合。
    结果缓存 6 小时。消耗约 25-35 WCL 点数（5 报告 x 5-7 查询）。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "balance-druid"
        encounter_id: 副本遭遇 ID
        key_level: 钥石等级

    Returns:
        MplusBenchmarkResponse 包含 meta + segments + cd_spacing
    """
    # Step 1: 检查缓存
    cache_key = f"mplus_bench:{spec}:{encounter_id}:k{key_level}:segments"
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("M+ benchmarks 缓存命中: %s", cache_key)
        return MplusBenchmarkResponse(**cached)

    # Step 2: 获取排行榜
    meta, entries = await query_mplus_rankings(
        client, encounter_id, spec, key_level
    )

    # Step 3: 无结果时返回空响应
    if not entries:
        logger.warning("M+ benchmarks: 无排行榜条目 %s/%d/k%d", spec, encounter_id, key_level)
        return MplusBenchmarkResponse(meta=meta, segments=[], cd_spacing=[])

    # Step 4: 从第一份报告推导 boss 名称
    boss_names = await _detect_boss_names(client, entries[0])

    # Step 5: 并行获取报告（Semaphore 限制并发）
    sem = asyncio.Semaphore(3)

    async def _fetch_one(entry: MplusRankingEntry) -> dict | None:
        async with sem:
            try:
                return await _fetch_report_benchmark_data(
                    client, entry, spec, boss_names
                )
            except Exception as exc:
                logger.warning("报告 %s 获取失败: %s", entry.report_code, exc)
                return None

    results = await asyncio.gather(*[_fetch_one(e) for e in entries])
    valid = [r for r in results if r is not None]

    # Step 6: 结果不足时记录警告
    if len(valid) < 2:
        logger.warning(
            "M+ benchmarks: 有效报告仅 %d 份（%s/%d/k%d）",
            len(valid), spec, encounter_id, key_level,
        )

    # Step 7: 空结果直接返回
    if not valid:
        return MplusBenchmarkResponse(meta=meta, segments=[], cd_spacing=[])

    # Step 8: 聚合
    segments = _aggregate_segment_data(valid)

    # Step 9: CD 分布模式
    cd_spacing = _compute_cd_spacing(
        {seg.position: seg.cd_casts for seg in segments}
    )

    # Step 10: 构建响应并缓存
    response = MplusBenchmarkResponse(
        meta=meta, segments=segments, cd_spacing=cd_spacing
    )
    cache_set(cache_key, response.model_dump())

    logger.info(
        "M+ benchmarks 完成: %s/%d/k%d, %d 段落, %d 报告",
        spec, encounter_id, key_level, len(segments), len(valid),
    )
    return response


async def _detect_boss_names(
    client: WCLClient, entry: MplusRankingEntry,
) -> list[str]:
    """从第一份报告推导 boss 名称列表。"""
    try:
        fights, _ = await _query_all_fights(client, entry.report_code)
        return [
            f.get("name", "")
            for f in fights
            if f.get("encounterID", 0) > 0 and f.get("name")
        ]
    except Exception as exc:
        logger.warning("Boss 名称检测失败 %s: %s", entry.report_code, exc)
        return []
