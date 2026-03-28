# ============================================================
# M+ Comparison Engine 测试
#
# 覆盖 Phase 10 Plan 01 需求:
#   DMG-02 (trash segment damage comparison)
#   INT-02 (interrupt comparison with critical missed targets)
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from src.models import (
    SegmentDamageBreakdown,
    SegmentDamageGap,
)


# ============================================================
# Trash Damage Comparison Tests (DMG-02)
# ============================================================


class TestTrashDamageComparison:
    """Trash 段落伤害对比 — 按技能计算差距百分比。"""

    def test_basic_damage_gap_flagged(self):
        """玩家技能 damage_pct 低于 benchmark 超过 20% 时标记 flagged=True。"""
        from src.tools.mplus_comparison import _compare_trash_damage

        player = [
            SegmentDamageBreakdown(spell_name="Starsurge", spell_id=78674, damage_pct=20.0),
            SegmentDamageBreakdown(spell_name="Starfire", spell_id=194153, damage_pct=30.0),
        ]
        bench = [
            SegmentDamageBreakdown(spell_name="Starsurge", spell_id=78674, damage_pct=50.0),
            SegmentDamageBreakdown(spell_name="Starfire", spell_id=194153, damage_pct=35.0),
        ]
        gaps = _compare_trash_damage(player, bench)

        # Starsurge: gap = 50.0 - 20.0 = 30.0 > 20 => flagged
        starsurge = next(g for g in gaps if g.spell_id == 78674)
        assert starsurge.gap_pct == 30.0
        assert starsurge.flagged is True

        # Starfire: gap = 35.0 - 30.0 = 5.0 <= 20 => not flagged
        starfire = next(g for g in gaps if g.spell_id == 194153)
        assert starfire.gap_pct == 5.0
        assert starfire.flagged is False

    def test_spell_in_player_not_in_benchmark(self):
        """玩家有但 benchmark 没有的技能 => gap_pct=0.0, flagged=False。"""
        from src.tools.mplus_comparison import _compare_trash_damage

        player = [
            SegmentDamageBreakdown(spell_name="Moonfire", spell_id=8921, damage_pct=15.0),
        ]
        bench: list[SegmentDamageBreakdown] = []
        gaps = _compare_trash_damage(player, bench)

        moonfire = next(g for g in gaps if g.spell_id == 8921)
        assert moonfire.gap_pct == 0.0
        assert moonfire.flagged is False

    def test_spell_in_benchmark_not_in_player(self):
        """Benchmark 有但玩家没有且 bench_pct > 5% => flagged=True。"""
        from src.tools.mplus_comparison import _compare_trash_damage

        player: list[SegmentDamageBreakdown] = []
        bench = [
            SegmentDamageBreakdown(spell_name="Starsurge", spell_id=78674, damage_pct=40.0),
        ]
        gaps = _compare_trash_damage(player, bench)

        starsurge = next(g for g in gaps if g.spell_id == 78674)
        assert starsurge.player_pct == 0.0
        assert starsurge.benchmark_pct == 40.0
        assert starsurge.flagged is True


# ============================================================
# Interrupt Comparison Tests (INT-02)
# ============================================================


class TestInterruptComparison:
    """打断对比 — 次数差距 + 关键未打断目标。"""

    def test_interrupt_count_gap_flagged(self):
        """玩家打断次数低于 benchmark 超过 20% 时标记。"""
        from src.tools.mplus_comparison import _compare_interrupts

        result = _compare_interrupts(
            player_count=3,
            player_targets={100, 200},
            bench_count_median=5.0,
            bench_targets={100, 200, 300},
        )
        # gap = (5.0 - 3) / 5.0 * 100 = 40.0% > 20 => flagged
        assert result["count_gap_pct"] == 40.0
        assert result["count_flagged"] is True

    def test_critical_missed_targets(self):
        """Benchmark 打断了 spell 300 但玩家没有 => critical missed。"""
        from src.tools.mplus_comparison import _compare_interrupts

        result = _compare_interrupts(
            player_count=3,
            player_targets={100, 200},
            bench_count_median=5.0,
            bench_targets={100, 200, 300},
        )
        assert 300 in result["critical_missed_target_ids"]

    def test_player_equal_or_more_not_flagged(self):
        """玩家打断次数 >= benchmark 中位数时不标记。"""
        from src.tools.mplus_comparison import _compare_interrupts

        result = _compare_interrupts(
            player_count=6,
            player_targets={100, 200, 300},
            bench_count_median=5.0,
            bench_targets={100, 200, 300},
        )
        # gap = (5.0 - 6) / 5.0 * 100 = -20.0 => not flagged
        assert result["count_flagged"] is False
        assert result["critical_missed_target_ids"] == []
