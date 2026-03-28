"""
M+ Comparison Engine — 玩家表现与基准数据的对比分析。

Pipeline: player data + benchmark data -> per-segment comparison -> gap flagging

公开接口:
  - _compute_gap(player_value, benchmark_value) -> dict
  - _compare_trash_damage(player_damage, bench_damage) -> list[SegmentDamageGap]
  - _compare_interrupts(player_count, player_targets, bench_count_median, bench_targets) -> dict
  - _compare_boss_casts(player_spell_counts, player_spell_names, player_duration, bench_spell_stats, kill) -> BossCastComparison
  - _compare_boss_cds(player_spell_counts, player_spell_names, tracked, fight_duration) -> list[dict]
  - _check_defensive_availability(death_ts, cast_events, tracked_spells) -> list[dict]
  - _build_death_breakdown(death_event, damage_taken_events, cast_events, tracked_spells, ...) -> DeathBreakdown
  - _query_damage_taken_events(client, report_code, start_time, end_time, target_id) -> list[dict]

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

import logging
from collections import defaultdict

from src.models import (
    BossCastComparison,
    DeathBreakdown,
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

        # 预期施放次数: 开战立刻用一次 + 之后每 CD 一次
        expected = 1 + int((fight_duration - 1) / cd_sec) if fight_duration > 0 else 1
        player_casts = player_spell_counts.get(sid, 0)
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
