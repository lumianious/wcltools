"""
M+ Coaching Tool — 将对比数据转化为可操作的教练建议。

Pipeline: MplusComparisonResponse -> per-segment coaching -> dungeon summary -> MplusCoachingResponse

公开接口:
  - coach_mplus_run(client, report_code, player_name, encounter_id, spec, key_level, fight) -> MplusCoachingResponse
  - _coach_trash_segment(seg: SegmentComparison) -> SegmentCoaching
  - _coach_boss_segment(boss: BossCastComparison) -> SegmentCoaching
  - _coach_deaths(deaths: list[DeathBreakdown]) -> list[CoachingItem]
  - _generate_trash_advice(gap: SegmentDamageGap, segment_name: str) -> str
  - _generate_boss_advice(gap: dict, boss_name: str) -> str
  - _build_dungeon_summary(seg_coaching, death_coaching, comparison_summary) -> DungeonCoachingSummary
  - _build_coaching_response(comparison: MplusComparisonResponse) -> MplusCoachingResponse

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

import logging

from src.models import (
    BossCastComparison,
    CoachingItem,
    DeathBreakdown,
    DungeonCoachingSummary,
    MplusCoachingResponse,
    MplusComparisonResponse,
    SegmentCoaching,
    SegmentComparison,
    SegmentDamageGap,
)

logger = logging.getLogger(__name__)


# ============================================================
# Trash 段落教练 (COACH-01 trash)
# ============================================================


def _generate_trash_advice(gap: SegmentDamageGap, segment_name: str) -> str:
    """生成 trash 段落的自然语言建议 — 包含技能名和 benchmark 百分比。"""
    return (
        f"In {segment_name}, your {gap.spell_name} was {gap.player_pct}% of damage "
        f"vs benchmark {gap.benchmark_pct}%. "
        f"Focus on using {gap.spell_name} more in AoE packs."
    )


def _generate_cd_advice(cd_gap: dict, segment_name: str) -> str:
    """生成 CD 差距的自然语言建议。"""
    spell = cd_gap.get("spell_name", "Unknown")
    bench = cd_gap.get("benchmark_median", 0)
    player = cd_gap.get("player_casts", 0)
    return (
        f"Use {spell} in {segment_name} — benchmark players cast it "
        f"{bench} times vs your {player}."
    )


def _coach_trash_segment(seg: SegmentComparison) -> SegmentCoaching:
    """将 trash 段落对比数据转化为教练建议。

    返回最多 3 个最高影响力的改进项，或在无 flagged 项时返回正面反馈。
    """
    items: list[CoachingItem] = []

    # 收集 flagged damage gaps，按 gap_pct 降序排列
    flagged_damage = sorted(
        [g for g in seg.damage_gaps if g.flagged],
        key=lambda g: g.gap_pct,
        reverse=True,
    )
    for gap in flagged_damage:
        items.append(CoachingItem(
            category="damage",
            spell_name=gap.spell_name,
            gap_pct=gap.gap_pct,
            player_value=gap.player_pct,
            benchmark_value=gap.benchmark_pct,
            coaching_text=_generate_trash_advice(gap, seg.segment_name),
        ))

    # 收集 flagged CD gaps
    flagged_cds = [cd for cd in seg.cd_gaps if cd.get("flagged")]
    flagged_cds.sort(key=lambda cd: cd.get("gap_pct", 0), reverse=True)
    for cd in flagged_cds:
        items.append(CoachingItem(
            category="cooldown",
            spell_name=cd.get("spell_name", ""),
            gap_pct=cd.get("gap_pct", 0.0),
            player_value=cd.get("player_casts", 0.0),
            benchmark_value=cd.get("benchmark_median", 0.0),
            coaching_text=_generate_cd_advice(cd, seg.segment_name),
        ))

    # 收集 flagged interrupt
    ic = seg.interrupt_comparison
    if ic.get("count_flagged"):
        gap_pct = ic.get("count_gap_pct", ic.get("gap_pct", 0.0))
        items.append(CoachingItem(
            category="interrupt",
            spell_name="Interrupt",
            gap_pct=gap_pct,
            player_value=ic.get("player_count", 0.0),
            benchmark_value=ic.get("benchmark_median", 0.0),
            coaching_text=(
                f"In {seg.segment_name}, your interrupt count was below benchmark. "
                f"Look for more interrupt opportunities on key casts."
            ),
        ))

    # 按 gap_pct 降序排列，取 top 3
    items.sort(key=lambda i: i.gap_pct, reverse=True)
    items = items[:3]

    # 无 flagged 项时返回正面反馈
    if not items:
        items = [CoachingItem(
            category="positive",
            coaching_text=(
                f"Good performance in {seg.segment_name}! "
                f"Your damage distribution matches or exceeds the benchmark."
            ),
        )]

    return SegmentCoaching(
        position=seg.position,
        segment_type="trash",
        segment_name=seg.segment_name,
        items=items,
    )


# ============================================================
# Boss 段落教练 (COACH-01 boss)
# ============================================================


def _generate_boss_advice(gap: dict, boss_name: str) -> str:
    """生成 boss 段落的自然语言建议 — 包含施法次数对比信息。"""
    spell = gap.get("spell_name", "Unknown")
    player = gap.get("player_casts", gap.get("player_count", 0))
    bench = gap.get("benchmark_casts", gap.get("bench_count", 0))
    return (
        f"On {boss_name}, cast {spell} more — "
        f"you had {player} casts vs benchmark {bench}."
    )


def _coach_boss_segment(boss: BossCastComparison) -> SegmentCoaching:
    """将 boss 对比数据转化为教练建议。

    合并 cast_gaps 和 cd_gaps，按 gap_pct 降序取 top 3。
    """
    items: list[CoachingItem] = []

    # 收集 flagged cast gaps
    flagged_casts = [g for g in boss.cast_gaps if g.get("flagged")]
    flagged_casts.sort(key=lambda g: g.get("gap_pct", 0), reverse=True)
    for gap in flagged_casts:
        items.append(CoachingItem(
            category="cast",
            spell_name=gap.get("spell_name", ""),
            gap_pct=gap.get("gap_pct", 0.0),
            player_value=gap.get("player_casts", 0.0),
            benchmark_value=gap.get("benchmark_casts", 0.0),
            coaching_text=_generate_boss_advice(gap, boss.boss_name),
        ))

    # 收集 flagged CD gaps
    flagged_cds = [g for g in boss.cd_gaps if g.get("flagged")]
    flagged_cds.sort(key=lambda g: g.get("gap_pct", 0), reverse=True)
    for cd in flagged_cds:
        items.append(CoachingItem(
            category="cooldown",
            spell_name=cd.get("spell_name", ""),
            gap_pct=cd.get("gap_pct", 0.0),
            player_value=cd.get("player_casts", 0.0),
            benchmark_value=cd.get("benchmark_median", 0.0),
            coaching_text=_generate_boss_advice(cd, boss.boss_name),
        ))

    # 按 gap_pct 降序，取 top 3
    items.sort(key=lambda i: i.gap_pct, reverse=True)
    items = items[:3]

    # 无 flagged 项时正面反馈
    if not items:
        items = [CoachingItem(
            category="positive",
            coaching_text=(
                f"Strong performance on {boss.boss_name}! "
                f"Your cast priorities and cooldown usage match the benchmark."
            ),
        )]

    return SegmentCoaching(
        position=boss.position,
        segment_type="boss",
        segment_name=boss.boss_name,
        items=items,
    )


# ============================================================
# 死亡教练 (COACH-03 survival)
# ============================================================


def _coach_deaths(deaths: list[DeathBreakdown]) -> list[CoachingItem]:
    """将死亡分析转化为教练建议。

    每次死亡生成一条建议，包含时间、段落、伤害来源和防御技能状态。
    """
    items: list[CoachingItem] = []
    for death in deaths:
        # 最大伤害来源
        top_source = ""
        if death.damage_taken_sources:
            top = death.damage_taken_sources[0]
            top_source = top.get("spell_name", "Unknown")

        # 可用但未使用的防御技能
        available_defensives = [
            d.get("spell_name", "Unknown")
            for d in death.defensive_status
            if d.get("status") in ("available_never_used", "available_off_cooldown")
        ]

        # 构建建议文本
        parts = [f"Died at {death.death_time_sec:.0f}s in {death.segment_name}."]
        if top_source:
            parts.append(f"Top damage source: {top_source}.")
        if available_defensives:
            parts.append(
                f"Available defensive(s) not used: {', '.join(available_defensives)}."
            )
        else:
            parts.append("All defensives were on cooldown.")

        items.append(CoachingItem(
            category="death",
            coaching_text=" ".join(parts),
        ))

    return items


# ============================================================
# 副本汇总 (COACH-02)
# ============================================================


def _build_dungeon_summary(
    seg_coaching: list[SegmentCoaching],
    death_coaching: list[CoachingItem],
    comparison_summary: dict,
) -> DungeonCoachingSummary:
    """构建整个副本的教练汇总。

    从 comparison_summary 取 flag 计数，收集所有非 positive 的 coaching items，
    按 gap_pct 降序取 top 5 作为 top_improvements。
    """
    total_damage = comparison_summary.get("total_damage_flags", 0)
    total_cd = comparison_summary.get("total_cd_flags", 0)
    total_deaths = comparison_summary.get("total_deaths", 0)
    total_interrupt = comparison_summary.get("total_interrupt_flags", 0)

    # 收集所有非 positive 的 segment coaching items
    all_items: list[CoachingItem] = []
    for seg in seg_coaching:
        for item in seg.items:
            if item.category != "positive":
                all_items.append(item)

    # 也包含 death coaching items
    all_items.extend(death_coaching)

    # 按 gap_pct 降序取 top 5
    all_items.sort(key=lambda i: i.gap_pct, reverse=True)
    top_improvements = all_items[:5]

    # 构建整体 NL 文本
    issue_parts = []
    if total_damage:
        issue_parts.append(f"{total_damage} damage issues")
    if total_cd:
        issue_parts.append(f"{total_cd} CD issues")
    if total_deaths:
        issue_parts.append(f"{total_deaths} deaths")
    if total_interrupt:
        issue_parts.append(f"{total_interrupt} interrupt issues")

    overall_text = ", ".join(issue_parts) + "." if issue_parts else "No major issues found."
    if top_improvements:
        overall_text += f" Biggest area: {top_improvements[0].spell_name or top_improvements[0].category}."

    return DungeonCoachingSummary(
        total_damage_flags=total_damage,
        total_cd_flags=total_cd,
        total_deaths=total_deaths,
        total_interrupt_flags=total_interrupt,
        top_improvements=top_improvements,
        overall_coaching_text=overall_text,
    )


# ============================================================
# 完整管道: comparison -> coaching response
# ============================================================


def _build_coaching_response(comparison: MplusComparisonResponse) -> MplusCoachingResponse:
    """将 MplusComparisonResponse 转化为 MplusCoachingResponse。

    遍历所有段落和 boss，生成 coaching items，然后构建汇总。
    """
    segment_coaching: list[SegmentCoaching] = []

    # Trash 段落教练
    for seg in comparison.segment_comparisons:
        segment_coaching.append(_coach_trash_segment(seg))

    # Boss 段落教练
    for boss in comparison.boss_comparisons:
        segment_coaching.append(_coach_boss_segment(boss))

    # 死亡教练
    death_coaching = _coach_deaths(comparison.death_analysis)

    # 副本汇总
    summary = _build_dungeon_summary(segment_coaching, death_coaching, comparison.summary)

    return MplusCoachingResponse(
        report_code=comparison.report_code,
        player_name=comparison.player_name,
        spec=comparison.spec,
        dungeon_name=comparison.dungeon_name,
        key_level=comparison.key_level,
        benchmark_key_level=comparison.benchmark_key_level,
        segment_coaching=segment_coaching,
        death_coaching=death_coaching,
        summary=summary,
    )


# ============================================================
# MCP 工具入口
# ============================================================


async def coach_mplus_run(
    client,
    report_code: str,
    player_name: str,
    encounter_id: int,
    spec: str,
    key_level: int,
    fight: str = "last",
) -> MplusCoachingResponse:
    """M+ 副本教练工具 — 调用 compare_mplus_run 获取对比数据，然后生成教练建议。

    Parameters:
        client: WCL API 客户端
        report_code: WCL 报告代码
        player_name: 玩家名称
        encounter_id: 副本 encounter ID
        spec: 专精名称（如 "Balance Druid"）
        key_level: 钥石等级
        fight: 战斗选择策略，默认 "last"
    """
    from src.tools.mplus_comparison import compare_mplus_run

    comparison = await compare_mplus_run(
        client, report_code, player_name, encounter_id, spec, key_level, fight,
    )
    return _build_coaching_response(comparison)
