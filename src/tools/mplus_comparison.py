"""
M+ Comparison Engine — 玩家表现与基准数据的对比分析。

Pipeline: player data + benchmark data -> per-segment comparison -> gap flagging

公开接口:
  - _compute_gap(player_value, benchmark_value) -> dict
  - _compare_trash_damage(player_damage, bench_damage) -> list[SegmentDamageGap]
  - _compare_interrupts(player_count, player_targets, bench_count_median, bench_targets) -> dict

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

from src.models import SegmentDamageBreakdown, SegmentDamageGap

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
