"""
M+ Comparison Engine — 玩家表现与基准数据的对比分析。

Pipeline: player data + benchmark data -> per-segment comparison -> gap flagging

公开接口:
  - compare_mplus_run(client, report_code, player_name, encounter_id, spec, key_level, fight) -> MplusComparisonResponse
  - _compute_gap(player_value, benchmark_value) -> dict
  - _compare_trash_damage(player_damage, bench_damage) -> list[SegmentDamageGap]
  - _compare_interrupts(player_count, player_targets, bench_count_median, bench_targets) -> dict
  - _compare_boss_casts(player_spell_counts, player_spell_names, player_duration, bench_spell_stats, kill) -> BossCastComparison
  - _compare_boss_cds(player_spell_counts, player_spell_names, tracked, fight_duration) -> list[dict]
  - _check_defensive_availability(death_ts, cast_events, tracked_spells) -> list[dict]
  - _build_death_breakdown(death_event, damage_taken_events, cast_events, tracked_spells, ...) -> DeathBreakdown
  - _query_damage_taken_events(client, report_code, start_time, end_time, target_id) -> list[dict]
  - _align_segments(player_segs, bench_segs) -> list[tuple]
  - _extract_player_segment_data(client, report_code, segment, source_id, tracked) -> dict
  - _build_segment_comparison(player_data, bench_seg) -> SegmentComparison
  - _analyze_player_deaths(client, report_code, run_start, run_end, source_id, tracked, segments, max_deaths) -> list[DeathBreakdown]
  - _build_summary(segment_comparisons, boss_comparisons, death_analysis, interrupt_summary) -> dict

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

import logging
from collections import defaultdict

from src.models import (
    BossCastComparison,
    DeathBreakdown,
    MplusBenchmarkSegment,
    MplusComparisonResponse,
    SegmentComparison,
    SegmentDamageBreakdown,
    SegmentDamageGap,
)

logger = logging.getLogger(__name__)

# ============================================================
# 通用差距计算
# ============================================================


def _compute_gap(player_value: float, benchmark_value: float) -> dict:
    """计算玩家值与基准值的差距百分比。

    gap_pct > 0 表示玩家低于基准，> 20 标记为 flagged。
    """
    if benchmark_value <= 0:
        return {"gap_pct": 0.0, "flagged": False}
    gap_pct = round((benchmark_value - player_value) / benchmark_value * 100, 1)
    flagged = gap_pct > 20.0
    return {"gap_pct": gap_pct, "flagged": flagged}


# ============================================================
# Trash 段落伤害对比 (DMG-02)
# ============================================================


def _compare_trash_damage(
    player_damage: list[SegmentDamageBreakdown],
    bench_damage: list[SegmentDamageBreakdown],
) -> list[SegmentDamageGap]:
    """按技能对比玩家与基准的伤害分布百分比。

    - 匹配依据: spell_id
    - gap_pct = bench.damage_pct - player.damage_pct
    - flagged: gap_pct > 20.0
    - benchmark 有但玩家没有: flagged=True if bench_pct > 5.0
    - 玩家有但 benchmark 没有: gap_pct=0.0, flagged=False
    """
    bench_by_id = {d.spell_id: d for d in bench_damage}
    player_by_id = {d.spell_id: d for d in player_damage}
    seen_ids: set[int] = set()
    gaps: list[SegmentDamageGap] = []

    # --- 玩家有的技能 ---
    for p in player_damage:
        seen_ids.add(p.spell_id)
        b = bench_by_id.get(p.spell_id)
        if b is not None:
            gap_pct = round(b.damage_pct - p.damage_pct, 1)
            flagged = gap_pct > 20.0
            gaps.append(SegmentDamageGap(
                spell_name=p.spell_name,
                spell_id=p.spell_id,
                player_pct=p.damage_pct,
                benchmark_pct=b.damage_pct,
                gap_pct=gap_pct,
                flagged=flagged,
            ))
        else:
            # 玩家有但 benchmark 没有 => 无法计算差距
            gaps.append(SegmentDamageGap(
                spell_name=p.spell_name,
                spell_id=p.spell_id,
                player_pct=p.damage_pct,
                benchmark_pct=0.0,
                gap_pct=0.0,
                flagged=False,
            ))

    # --- benchmark 有但玩家没有 ---
    for b in bench_damage:
        if b.spell_id not in seen_ids:
            flagged = b.damage_pct > 5.0
            gaps.append(SegmentDamageGap(
                spell_name=b.spell_name,
                spell_id=b.spell_id,
                player_pct=0.0,
                benchmark_pct=b.damage_pct,
                gap_pct=round(b.damage_pct, 1),
                flagged=flagged,
            ))

    return gaps


# ============================================================
# 打断对比 (INT-02)
# ============================================================


def _compare_interrupts(
    player_count: int,
    player_targets: set[int],
    bench_count_median: float,
    bench_targets: set[int],
) -> dict:
    """对比玩家与基准的打断数据。

    - count_gap: 次数差距百分比（使用 _compute_gap）
    - critical_missed: benchmark 打断了但玩家没打断的 spell_id 列表
    """
    count_gap = _compute_gap(float(player_count), bench_count_median)
    critical_missed = bench_targets - player_targets
    return {
        "player_count": player_count,
        "benchmark_median": bench_count_median,
        "count_gap_pct": count_gap["gap_pct"],
        "count_flagged": count_gap["flagged"],
        "critical_missed_target_ids": sorted(critical_missed),
    }


# ============================================================
# Boss 施法对比 (BOSS-01)
# ============================================================


def _compare_boss_casts(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    player_duration: float,
    bench_spell_stats: list[dict],
    kill: bool = True,
) -> BossCastComparison:
    """按技能对比玩家与基准的 boss 施法次数。

    对每个 benchmark 技能，计算玩家 cast count 差距百分比。
    gap_pct > 20% 时标记 flagged=True。

    Args:
        player_spell_counts: {spell_id: cast_count}
        player_spell_names: {spell_id: spell_name}
        player_duration: 玩家战斗时长（秒）
        bench_spell_stats: benchmark boss cast stats 列表
        kill: 是否击杀（False 表示灭团）

    Returns:
        BossCastComparison 包含 cast_gaps 和 status
    """
    dur_min = player_duration / 60.0 if player_duration > 0 else 1.0
    cast_gaps: list[dict] = []

    for bs in bench_spell_stats:
        sid = bs["spell_id"]
        bench_casts = bs["cast_count"]
        bench_cpm = bs.get("cpm", 0.0)
        spell_name = player_spell_names.get(sid) or bs.get("spell_name", f"Spell#{sid}")

        player_casts = player_spell_counts.get(sid, 0)

        # 跳过玩家未施放的技能（可能没有对应天赋）
        if player_casts == 0:
            continue

        player_cpm = round(player_casts / dur_min, 2)

        gap = _compute_gap(float(player_casts), float(bench_casts))

        cast_gaps.append({
            "spell_name": spell_name,
            "spell_id": sid,
            "player_casts": player_casts,
            "benchmark_casts": bench_casts,
            "cpm_player": player_cpm,
            "cpm_benchmark": bench_cpm,
            "gap_pct": gap["gap_pct"],
            "flagged": gap["flagged"],
        })

    status = "complete" if kill else "incomplete"
    return BossCastComparison(
        boss_name="",
        cast_gaps=cast_gaps,
        status=status,
    )


# ============================================================
# Boss CD 对比 (BOSS-02)
# ============================================================

# 需要 CD 对比的 ability_type 类别
_CD_ABILITY_TYPES = {"offensive_1min", "offensive_2min", "offensive_3min"}


def _compare_boss_cds(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    tracked: dict[int, dict],
    fight_duration: float,
) -> list[dict]:
    """对比玩家 boss 战斗中的大 CD 使用情况。

    根据战斗时长和 CD 时间计算预期施放次数，与玩家实际使用对比。
    只检查玩家实际施放过的 CD 技能（即出现在 player_spell_counts 中），
    避免标记玩家没有天赋的技能。

    Args:
        player_spell_counts: {spell_id: cast_count}
        player_spell_names: {spell_id: spell_name}
        tracked: tracked spell 字典 {spell_id: {name, cd_seconds, ability_type}}
        fight_duration: 战斗时长（秒）

    Returns:
        cd_gaps 列表: [{spell_name, spell_id, player_casts, expected_casts, missed_uses}]
    """
    cd_gaps: list[dict] = []

    for sid, info in tracked.items():
        atype = info.get("ability_type", "")
        if atype not in _CD_ABILITY_TYPES:
            continue

        cd_sec = info.get("cd_seconds", 0)
        if cd_sec <= 0:
            continue

        # 只检查玩家实际施放过的 CD（避免标记无天赋技能）
        player_casts = player_spell_counts.get(sid, 0)
        if player_casts == 0:
            continue

        # 预期施放次数: 开战立刻用一次 + 之后每 CD 一次
        expected = 1 + int((fight_duration - 1) / cd_sec) if fight_duration > 0 else 1
        missed = max(0, expected - player_casts)

        if missed > 0:
            spell_name = player_spell_names.get(sid) or info.get("name", f"Spell#{sid}")
            cd_gaps.append({
                "spell_name": spell_name,
                "spell_id": sid,
                "player_casts": player_casts,
                "expected_casts": expected,
                "missed_uses": missed,
            })

    return cd_gaps


# ============================================================
# 防御技能可用性检查 (SURV-02)
# ============================================================

# 需要检查的防御 ability_type 类别
_DEFENSIVE_ABILITY_TYPES = {"defensive", "raid_cd"}


def _check_defensive_availability(
    death_ts: int,
    cast_events: list[dict],
    tracked_spells: dict[int, dict],
    segment_start: int = 0,
) -> list[dict]:
    """检查死亡时每个防御技能的可用状态。

    三种状态:
    - available_never_used: 从未施放
    - on_cooldown: 死亡时 CD 未恢复
    - available_off_cooldown: CD 已恢复但未使用

    Args:
        death_ts: 死亡时间戳（毫秒）
        cast_events: 施法事件列表
        tracked_spells: tracked spell 字典
        segment_start: 段落起始时间（毫秒），用于计算相对时间

    Returns:
        [{spell_name, spell_id, status, last_cast_sec}]
    """
    # 预处理: 按 spell_id 索引最后一次施放时间
    last_cast_by_spell: dict[int, int] = {}
    for ev in cast_events:
        sid = ev.get("abilityGameID", 0)
        ts = ev.get("timestamp", 0)
        if sid in tracked_spells and ts <= death_ts:
            if sid not in last_cast_by_spell or ts > last_cast_by_spell[sid]:
                last_cast_by_spell[sid] = ts

    results: list[dict] = []
    for sid, info in tracked_spells.items():
        atype = info.get("ability_type", "")
        if atype not in _DEFENSIVE_ABILITY_TYPES:
            continue

        spell_name = info.get("name", f"Spell#{sid}")
        cd_ms = info.get("cd_seconds", 0) * 1000

        last_ts = last_cast_by_spell.get(sid)
        if last_ts is None:
            status = "available_never_used"
            last_cast_sec = None
        elif last_ts + cd_ms > death_ts:
            status = "on_cooldown"
            last_cast_sec = round((last_ts - segment_start) / 1000.0, 1)
        else:
            status = "available_off_cooldown"
            last_cast_sec = round((last_ts - segment_start) / 1000.0, 1)

        results.append({
            "spell_name": spell_name,
            "spell_id": sid,
            "status": status,
            "last_cast_sec": last_cast_sec,
        })

    return results


# ============================================================
# 死亡分析 (SURV-02)
# ============================================================

# 每次死亡最多记录的 damage-taken 来源数
_MAX_DAMAGE_SOURCES = 5


def _build_death_breakdown(
    death_event: dict,
    damage_taken_events: list[dict],
    cast_events: list[dict],
    tracked_spells: dict[int, dict],
    segment_position: int,
    segment_name: str,
    run_start_time: int,
) -> DeathBreakdown:
    """构建单次死亡的详细分析。

    合并 damage-taken 来源（按总量降序）与防御技能可用性。

    Args:
        death_event: 死亡事件 dict（含 timestamp）
        damage_taken_events: DamageTaken 事件列表
        cast_events: 施法事件列表（用于判断防御技能状态）
        tracked_spells: tracked spell 字典
        segment_position: 段落位置索引
        segment_name: 段落名称
        run_start_time: 副本开始时间（毫秒），用于计算相对时间

    Returns:
        DeathBreakdown
    """
    death_ts = death_event.get("timestamp", 0)
    death_time_sec = round((death_ts - run_start_time) / 1000.0, 1)

    # --- damage-taken 来源汇总 ---
    source_totals: dict[int, dict] = {}
    for ev in damage_taken_events:
        sid = ev.get("abilityGameID", 0)
        amount = ev.get("amount", 0)
        name = ""
        ability = ev.get("ability")
        if isinstance(ability, dict):
            name = ability.get("name", "")
        if sid not in source_totals:
            source_totals[sid] = {"spell_name": name, "spell_id": sid, "amount": 0}
        source_totals[sid]["amount"] += amount

    # 按伤害量降序，取前 N
    damage_sources = sorted(
        source_totals.values(), key=lambda x: x["amount"], reverse=True
    )[:_MAX_DAMAGE_SOURCES]

    # --- 防御技能可用性 ---
    defensive_status = _check_defensive_availability(
        death_ts, cast_events, tracked_spells, segment_start=run_start_time,
    )

    return DeathBreakdown(
        death_time_sec=death_time_sec,
        segment_position=segment_position,
        segment_name=segment_name,
        damage_taken_sources=damage_sources,
        defensive_status=defensive_status,
    )


# ============================================================
# DamageTaken 查询 (WCL async)
# ============================================================

# 单个副本最多分析的死亡次数（Pitfall 5）
_MAX_DEATHS_PER_RUN = 5


async def _query_damage_taken_events(
    client,
    report_code: str,
    start_time: int,
    end_time: int,
    target_id: int,
) -> list[dict]:
    """查询 DamageTaken 事件。

    使用 targetID（非 sourceID）— DamageTaken 以受伤者为目标过滤。

    Args:
        client: WCL API 客户端
        report_code: 报告代码
        start_time: 开始时间（毫秒）
        end_time: 结束时间（毫秒）
        target_id: 目标玩家 ID（受伤者）

    Returns:
        DamageTaken 事件列表
    """
    all_events: list[dict] = []
    next_ts: int | None = start_time
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


# ============================================================
# 段落对齐 — 按 position 匹配玩家与基准段落
# ============================================================


def _align_segments(
    player_segs: list[dict],
    bench_segs: list[MplusBenchmarkSegment],
) -> list[tuple[dict, MplusBenchmarkSegment | None]]:
    """按 position 将玩家段落与基准段落配对。

    玩家可能有多余段落（基准无对应），此时返回 (player_seg, None)。
    """
    bench_by_pos = {s.position: s for s in bench_segs}
    return [(seg, bench_by_pos.get(seg["position"])) for seg in player_segs]


# ============================================================
# 玩家段落数据提取 (async)
# ============================================================


async def _extract_player_segment_data(
    client,
    report_code: str,
    segment: dict,
    source_id: int,
    tracked: dict[int, dict],
) -> dict:
    """提取单个玩家段落的 damage/CD/interrupt 数据。

    对 trash 段落: 查询 damage table + cast events + interrupt events。
    对 boss 段落: 查询 cast events（用于 cast-by-cast 对比）。
    """
    from src.tools.mplus_benchmarks import (
        _count_segment_interrupts,
        _extract_segment_cds,
        _extract_segment_damage,
        _query_segment_cast_events,
        _query_segment_damage_table,
        _query_segment_interrupt_events,
    )

    start = segment["start_time"]
    end = segment["end_time"]
    seg_type = segment["segment_type"]

    result = {
        "position": segment["position"],
        "segment_type": seg_type,
        "name": segment["name"],
        "start_time": start,
        "end_time": end,
        "duration_sec": round((end - start) / 1000.0, 1),
    }

    if seg_type == "trash":
        # 三类数据并行查询
        damage_entries = await _query_segment_damage_table(
            client, report_code, start, end, source_id
        )
        cast_events = await _query_segment_cast_events(
            client, report_code, start, end, source_id
        )
        interrupt_events = await _query_segment_interrupt_events(
            client, report_code, start, end, source_id
        )

        result["damage_breakdown"] = _extract_segment_damage(damage_entries)
        offensive, defensive = _extract_segment_cds(cast_events, tracked)
        result["cd_casts"] = offensive
        result["defensive_cds"] = defensive
        result["interrupt_count"] = _count_segment_interrupts(interrupt_events)
        # 提取打断目标 spell_id 集合
        result["interrupt_target_ids"] = {
            ev.get("abilityGameID", 0) for ev in interrupt_events
        }
    else:
        # boss 段落: 查询 cast events 用于 cast-by-cast 对比
        cast_events = await _query_segment_cast_events(
            client, report_code, start, end, source_id
        )
        # 统计施法次数和名称
        spell_counts: dict[int, int] = defaultdict(int)
        spell_names: dict[int, str] = {}
        for ev in cast_events:
            sid = ev.get("abilityGameID", 0)
            spell_counts[sid] += 1
            if sid not in spell_names:
                ability = ev.get("ability")
                name = ability.get("name", "") if isinstance(ability, dict) else ""
                if not name:
                    info = tracked.get(sid)
                    name = info["name"] if info else f"Spell#{sid}"
                spell_names[sid] = name

        result["spell_counts"] = dict(spell_counts)
        result["spell_names"] = spell_names
        result["cast_events"] = cast_events
        # boss 段落也查询 CD 数据用于 BOSS-02
        offensive, defensive = _extract_segment_cds(cast_events, tracked)
        result["cd_casts"] = offensive
        result["defensive_cds"] = defensive

    return result


# ============================================================
# 段落对比构建
# ============================================================


def _build_segment_comparison(
    player_data: dict,
    bench_seg: MplusBenchmarkSegment | None,
) -> SegmentComparison:
    """构建单个 trash 段落的对比结果。

    如果无对应 benchmark，标记 status="no_benchmark"。
    """
    pos = player_data["position"]
    seg_type = player_data["segment_type"]
    seg_name = player_data["name"]

    if bench_seg is None:
        return SegmentComparison(
            position=pos,
            segment_type=seg_type,
            segment_name=seg_name,
            status="no_benchmark",
        )

    # --- 伤害对比 ---
    damage_gaps = _compare_trash_damage(
        player_data.get("damage_breakdown", []),
        bench_seg.damage_breakdown,
    )

    # --- CD 对比 ---
    cd_gaps: list[dict] = []
    player_cds = player_data.get("cd_casts", [])
    bench_cds = bench_seg.cd_casts
    bench_cd_by_id = {cd.spell_id: cd for cd in bench_cds}
    player_cd_by_id = {}
    for cd in player_cds:
        # 支持 dict 或 SegmentCDCast 对象
        sid = cd.spell_id if hasattr(cd, "spell_id") else cd.get("spell_id", 0)
        name = cd.spell_name if hasattr(cd, "spell_name") else cd.get("spell_name", "")
        count = cd.cast_count_median if hasattr(cd, "cast_count_median") else cd.get("cast_count_median", 0)
        player_cd_by_id[sid] = {"spell_name": name, "count": count}

    for sid, bcd in bench_cd_by_id.items():
        p_info = player_cd_by_id.get(sid)
        p_count = p_info["count"] if p_info else 0.0
        # 跳过玩家未施放过的 CD（可能没有该天赋）
        if p_info is None:
            continue
        # 跳过双方都为 0 的无意义对比
        if p_count == 0 and bcd.cast_count_median == 0:
            continue
        gap = _compute_gap(float(p_count), float(bcd.cast_count_median))
        cd_gaps.append({
            "spell_name": bcd.spell_name,
            "spell_id": sid,
            "player_casts": p_count,
            "benchmark_median": bcd.cast_count_median,
            "gap_pct": gap["gap_pct"],
            "flagged": gap["flagged"],
        })

    # --- 打断对比 ---
    interrupt_comp = _compare_interrupts(
        player_count=player_data.get("interrupt_count", 0),
        player_targets=player_data.get("interrupt_target_ids", set()),
        bench_count_median=bench_seg.interrupt_count_median,
        bench_targets=set(),  # benchmark 不存储个别目标 ID
    )

    return SegmentComparison(
        position=pos,
        segment_type=seg_type,
        segment_name=seg_name,
        status="compared",
        damage_gaps=damage_gaps,
        cd_gaps=cd_gaps,
        interrupt_comparison=interrupt_comp,
    )


# ============================================================
# 死亡分析编排 (async)
# ============================================================


async def _analyze_player_deaths(
    client,
    report_code: str,
    run_start: int,
    run_end: int,
    source_id: int,
    tracked: dict[int, dict],
    segments: list[dict],
    max_deaths: int = _MAX_DEATHS_PER_RUN,
) -> list[DeathBreakdown]:
    """分析玩家死亡事件，返回 DeathBreakdown 列表。

    Cap 最多 max_deaths 次（默认 5），控制 API 预算。
    """
    from src.tools.analyze import _query_death_events
    from src.tools.mplus_benchmarks import _query_segment_cast_events

    death_events = await _query_death_events(
        client, report_code, run_start, run_end, source_id
    )
    death_events = death_events[:max_deaths]

    if not death_events:
        return []

    # 查询整个副本的 cast events（用于防御技能可用性判断）
    cast_events = await _query_segment_cast_events(
        client, report_code, run_start, run_end, source_id
    )

    results: list[DeathBreakdown] = []
    for death_ev in death_events:
        death_ts = death_ev.get("timestamp", 0)

        # 确定死亡所在段落
        seg_pos = 0
        seg_name = "Unknown"
        for seg in segments:
            if seg["start_time"] <= death_ts <= seg["end_time"]:
                seg_pos = seg["position"]
                seg_name = seg["name"]
                break

        # 查询死亡前 15s 的 DamageTaken 事件
        dt_start = max(death_ts - 15000, run_start)
        damage_taken = await _query_damage_taken_events(
            client, report_code, dt_start, death_ts, source_id
        )

        bd = _build_death_breakdown(
            death_event=death_ev,
            damage_taken_events=damage_taken,
            cast_events=cast_events,
            tracked_spells=tracked,
            segment_position=seg_pos,
            segment_name=seg_name,
            run_start_time=run_start,
        )
        results.append(bd)

    return results


# ============================================================
# 汇总构建
# ============================================================


def _build_summary(
    segment_comparisons: list[SegmentComparison],
    boss_comparisons: list[BossCastComparison],
    death_analysis: list[DeathBreakdown],
    interrupt_summary: dict,
) -> dict:
    """构建对比汇总 — 统计各类 flag 数量和最差段落。"""
    total_damage_flags = 0
    total_cd_flags = 0
    total_interrupt_flags = 0
    segment_flag_counts: list[tuple[int, str, int]] = []

    for sc in segment_comparisons:
        if sc.status == "no_benchmark":
            continue
        dmg_flags = sum(1 for g in sc.damage_gaps if g.flagged)
        cd_flags = sum(1 for g in sc.cd_gaps if g.get("flagged", False))
        int_flagged = 1 if sc.interrupt_comparison.get("count_flagged", False) else 0

        total_damage_flags += dmg_flags
        total_cd_flags += cd_flags
        total_interrupt_flags += int_flagged

        seg_total = dmg_flags + cd_flags + int_flagged
        segment_flag_counts.append((sc.position, sc.segment_name, seg_total))

    # boss comparison flags
    for bc in boss_comparisons:
        cd_flags = sum(1 for g in bc.cd_gaps if g.get("flagged", False))
        total_cd_flags += cd_flags

    # 最差 3 个段落
    segment_flag_counts.sort(key=lambda x: x[2], reverse=True)
    worst_segments = [
        {"position": pos, "name": name, "flag_count": count}
        for pos, name, count in segment_flag_counts[:3]
        if count > 0
    ]

    return {
        "total_damage_flags": total_damage_flags,
        "total_cd_flags": total_cd_flags,
        "total_deaths": len(death_analysis),
        "total_interrupt_flags": total_interrupt_flags,
        "worst_segments": worst_segments,
    }


# ============================================================
# 编排器: compare_mplus_run (MCP 工具入口)
# ============================================================


async def compare_mplus_run(
    client,
    report_code: str,
    player_name: str,
    encounter_id: int,
    spec: str,
    key_level: int,
    fight: str = "last",
) -> MplusComparisonResponse:
    """对比玩家 M+ 副本表现与顶尖玩家基准。

    Pipeline:
      a. 获取玩家副本数据 (fights -> group -> select)
      b. 获取 benchmark 数据 (get_mplus_benchmarks)
      c. 获取 source_id (masterData -> find_actor_id_ci)
      d. 构建 tracked spells
      e. 构建玩家段落 (boss-bounded position alignment)
      f. 逐段提取玩家数据
      g. 对齐并对比 (trash段) / cast-by-cast (boss段)
      h. 死亡分析
      i. 打断汇总
      j. 汇总
      k. 返回 MplusComparisonResponse

    Args:
        client: WCL API 客户端
        report_code: 报告代码
        player_name: 角色名（大小写不敏感）
        encounter_id: 副本遭遇 ID
        spec: 专精 slug，如 "balance-druid"
        key_level: 钥石等级
        fight: 选择哪个副本 run（默认 "last"）

    Returns:
        MplusComparisonResponse 完整对比结果
    """
    from src.tools._wcl_helpers import find_actor_id_ci
    from src.tools.dungeon_analysis import (
        _group_fights_by_dungeon,
        _query_all_fights,
        _query_dungeon_pulls,
        _select_dungeon_run,
    )
    from src.tools.mplus_benchmarks import (
        _build_segment_positions,
        _collect_segment_fights,
        _detect_boss_names,
        _extract_boss_benchmark,
        get_mplus_benchmarks,
    )
    from src.tools.timelines import _build_tracked_spells
    from src.tools.timelines import _query_master_data

    logger.info(
        "compare_mplus_run: %s in %s spec=%s encounter=%d key=%d fight=%s",
        player_name, report_code, spec, encounter_id, key_level, fight,
    )

    # ---- (a) 获取玩家副本数据 ----
    fights, _ = await _query_all_fights(client, report_code)
    runs = _group_fights_by_dungeon(fights)
    selected_run = _select_dungeon_run(runs, fight)
    dungeon_name = selected_run.zone_name

    # ---- (b) 获取 benchmark 数据 ----
    bench_resp = await get_mplus_benchmarks(client, spec, encounter_id, key_level)

    # ---- (c) 获取 source_id ----
    actors = await _query_master_data(client, report_code)
    source_id = find_actor_id_ci(actors, player_name)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player_name}' in report {report_code}")

    # ---- (d) 构建 tracked spells ----
    tracked = _build_tracked_spells(spec)

    # ---- (e) 构建玩家段落 (优先 dungeonPulls，回退旧模式) ----
    player_segs: list[dict] = []
    agg = selected_run.aggregate_fight
    if agg is not None:
        # 尝试 dungeonPulls 模式
        pulls = await _query_dungeon_pulls(
            client, report_code, agg["id"]
        )
        if pulls:
            player_segs = _build_segment_positions(pulls)

    if not player_segs:
        # 回退: 旧 top-level fights 模式
        seg_fights = _collect_segment_fights(selected_run, runs)
        if not seg_fights:
            raise ValueError(f"副本 '{dungeon_name}' 中无有效战斗段落")
        boss_names = await _detect_boss_names(client, report_code)
        player_segs = _build_segment_positions(seg_fights, boss_names)

    # ---- (f) 逐段提取玩家数据 ----
    player_seg_data: list[dict] = []
    for seg in player_segs:
        try:
            data = await _extract_player_segment_data(
                client, report_code, seg, source_id, tracked
            )
            player_seg_data.append(data)
        except Exception as exc:
            logger.warning("段落 %d 数据提取失败: %s", seg["position"], exc)

    # ---- (g) 对齐并对比 ----
    aligned = _align_segments(player_seg_data, bench_resp.segments)

    segment_comparisons: list[SegmentComparison] = []
    boss_comparisons: list[BossCastComparison] = []

    for p_seg, b_seg in aligned:
        if p_seg["segment_type"] == "trash":
            comp = _build_segment_comparison(p_seg, b_seg)
            segment_comparisons.append(comp)
        else:
            # Boss 段落: cast-by-cast 对比
            boss_name = p_seg["name"]
            duration_sec = p_seg["duration_sec"]

            # 找到 benchmark boss 段落的 cast_stats
            bench_cast_stats: list[dict] = []
            bench_dur = 0.0
            if b_seg is not None:
                # benchmark 段落有 cd_casts 但没有 cast_stats
                # 使用 cd_casts 作为 cast_stats 的来源
                for cd in b_seg.cd_casts:
                    bench_cast_stats.append({
                        "spell_id": cd.spell_id,
                        "spell_name": cd.spell_name,
                        "cast_count": int(cd.cast_count_median),
                        "cpm": 0.0,
                    })
                bench_dur = b_seg.duration_median

            # BOSS-01: cast-level 对比
            cast_comp = _compare_boss_casts(
                player_spell_counts=p_seg.get("spell_counts", {}),
                player_spell_names=p_seg.get("spell_names", {}),
                player_duration=duration_sec,
                bench_spell_stats=bench_cast_stats,
            )

            # BOSS-02: CD 对比
            cd_gaps = _compare_boss_cds(
                player_spell_counts=p_seg.get("spell_counts", {}),
                player_spell_names=p_seg.get("spell_names", {}),
                tracked=tracked,
                fight_duration=duration_sec,
            )

            boss_comparisons.append(BossCastComparison(
                boss_name=boss_name,
                position=p_seg["position"],
                player_duration_sec=duration_sec,
                benchmark_duration_sec=bench_dur,
                cast_gaps=cast_comp.cast_gaps,
                cd_gaps=cd_gaps,
                status=cast_comp.status,
            ))

    # ---- (h) 死亡分析 ----
    run_start = min(s["start_time"] for s in player_segs)
    run_end = max(s["end_time"] for s in player_segs)

    death_analysis = await _analyze_player_deaths(
        client, report_code, run_start, run_end,
        source_id, tracked, player_segs,
    )

    # ---- (i) 打断汇总 ----
    total_player_interrupts = sum(
        d.get("interrupt_count", 0) for d in player_seg_data
        if d["segment_type"] == "trash"
    )
    total_bench_interrupts = sum(
        s.interrupt_count_median for s in bench_resp.segments
        if s.segment_type == "trash"
    )
    interrupt_summary = _compare_interrupts(
        player_count=total_player_interrupts,
        player_targets=set(),
        bench_count_median=total_bench_interrupts,
        bench_targets=set(),
    )

    # ---- (j) 汇总 ----
    summary = _build_summary(
        segment_comparisons, boss_comparisons, death_analysis, interrupt_summary,
    )

    # ---- (k) 返回 ----
    return MplusComparisonResponse(
        report_code=report_code,
        player_name=player_name,
        spec=spec,
        dungeon_name=dungeon_name,
        key_level=key_level,
        benchmark_key_level=bench_resp.meta.actual_bracket or key_level,
        segment_comparisons=segment_comparisons,
        boss_comparisons=boss_comparisons,
        death_analysis=death_analysis,
        interrupt_summary=interrupt_summary,
        summary=summary,
    )
