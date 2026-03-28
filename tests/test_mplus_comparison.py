# ============================================================
# M+ Comparison Engine 测试
#
# 覆盖 Phase 10 需求:
#   DMG-02 (trash segment damage comparison)
#   INT-02 (interrupt comparison with critical missed targets)
#   BOSS-01 (boss cast-level comparison)
#   BOSS-02 (boss CD/defensive comparison)
#   SURV-02 (death analysis with defensive availability)
#   Orchestrator (segment alignment + summary + compare_mplus_run)
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from src.models import (
    DeathBreakdown,
    MplusBenchmarkSegment,
    SegmentComparison,
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


# ============================================================
# Boss Cast Comparison Tests (BOSS-01, BOSS-02)
# ============================================================


class TestBossComparison:
    """Boss 段落施法对比 — 按技能比较 cast count 差距。"""

    def test_boss_cast_comparison(self):
        """玩家 cast count 低于 benchmark 超过 20% 时标记 flagged=True。"""
        from src.tools.mplus_comparison import _compare_boss_casts

        player_spell_counts = {100: 5, 200: 3}
        player_spell_names = {100: "Starfire", 200: "Wrath"}
        player_duration = 120.0  # 2 分钟
        bench_spell_stats = [
            {"spell_id": 100, "spell_name": "Starfire", "cast_count": 8, "cpm": 4.0},
            {"spell_id": 200, "spell_name": "Wrath", "cast_count": 3, "cpm": 1.5},
        ]
        result = _compare_boss_casts(
            player_spell_counts, player_spell_names,
            player_duration, bench_spell_stats,
        )
        # Starfire: gap = (8-5)/8 = 37.5% > 20% => flagged
        starfire = next(g for g in result.cast_gaps if g["spell_id"] == 100)
        assert starfire["flagged"] is True
        assert starfire["gap_pct"] == 37.5

        # Wrath: gap = (3-3)/3 = 0% => not flagged
        wrath = next(g for g in result.cast_gaps if g["spell_id"] == 200)
        assert wrath["flagged"] is False

    def test_boss_wipe_status(self):
        """kill=False 时 status 应为 incomplete。"""
        from src.tools.mplus_comparison import _compare_boss_casts

        result = _compare_boss_casts(
            player_spell_counts={}, player_spell_names={},
            player_duration=60.0, bench_spell_stats=[],
            kill=False,
        )
        assert result.status == "incomplete"

    def test_boss_cd_comparison(self):
        """玩家 CD 使用次数低于预期时报告 missed_uses。"""
        from src.tools.mplus_comparison import _compare_boss_cds

        # 玩家用了 1 次 2min CD，战斗 240s => 预期 2 次
        player_spell_counts = {500: 1}
        player_spell_names = {500: "Incarnation"}
        tracked = {
            500: {"name": "Incarnation", "cd_seconds": 120, "ability_type": "offensive_2min"},
        }
        cd_gaps = _compare_boss_cds(
            player_spell_counts, player_spell_names,
            tracked, fight_duration=240.0,
        )
        inc = next(g for g in cd_gaps if g["spell_id"] == 500)
        assert inc["missed_uses"] == 1


# ============================================================
# Death Analysis Tests (SURV-02)
# ============================================================


class TestDeathAnalysis:
    """死亡分析 — damage-taken 来源 + 防御技能可用性。"""

    def test_death_breakdown_available_defensive(self):
        """防御技能从未施放 => status=available_never_used。"""
        from src.tools.mplus_comparison import _check_defensive_availability

        death_ts = 60000  # 60 秒
        cast_events: list[dict] = []  # 没有任何施法记录
        tracked = {
            500: {"name": "Barkskin", "cd_seconds": 120, "ability_type": "defensive"},
        }
        result = _check_defensive_availability(death_ts, cast_events, tracked)
        barkskin = next(d for d in result if d["spell_id"] == 500)
        assert barkskin["status"] == "available_never_used"

    def test_death_breakdown_on_cooldown(self):
        """防御技能在死亡前施放且 CD 未恢复 => status=on_cooldown。"""
        from src.tools.mplus_comparison import _check_defensive_availability

        death_ts = 60000  # 60s
        cast_events = [{"abilityGameID": 500, "timestamp": 50000}]  # 50s 时施放
        tracked = {
            500: {"name": "Barkskin", "cd_seconds": 120, "ability_type": "defensive"},
        }
        result = _check_defensive_availability(death_ts, cast_events, tracked)
        barkskin = next(d for d in result if d["spell_id"] == 500)
        # 50000 + 120*1000 = 170000 > 60000 => on_cooldown
        assert barkskin["status"] == "on_cooldown"

    def test_death_breakdown_off_cooldown(self):
        """防御技能已施放且 CD 已恢复 => status=available_off_cooldown。"""
        from src.tools.mplus_comparison import _check_defensive_availability

        death_ts = 200000  # 200s
        cast_events = [{"abilityGameID": 500, "timestamp": 50000}]  # 50s 时施放
        tracked = {
            500: {"name": "Barkskin", "cd_seconds": 120, "ability_type": "defensive"},
        }
        result = _check_defensive_availability(death_ts, cast_events, tracked)
        barkskin = next(d for d in result if d["spell_id"] == 500)
        # 50000 + 120*1000 = 170000 < 200000 => available_off_cooldown
        assert barkskin["status"] == "available_off_cooldown"

    def test_damage_taken_sources(self):
        """damage-taken 事件按伤害量降序排列。"""
        from src.tools.mplus_comparison import _build_death_breakdown
        from src.models import DeathBreakdown

        death_event = {"timestamp": 60000}
        damage_taken = [
            {"abilityGameID": 100, "ability": {"name": "Fireball"}, "amount": 5000},
            {"abilityGameID": 200, "ability": {"name": "Melee"}, "amount": 15000},
            {"abilityGameID": 300, "ability": {"name": "Frostbolt"}, "amount": 8000},
        ]
        result = _build_death_breakdown(
            death_event=death_event,
            damage_taken_events=damage_taken,
            cast_events=[],
            tracked_spells={},
            segment_position=1,
            segment_name="Boss #1",
            run_start_time=0,
        )
        assert isinstance(result, DeathBreakdown)
        # 按 amount 降序: Melee 15000, Frostbolt 8000, Fireball 5000
        assert result.damage_taken_sources[0]["amount"] == 15000
        assert result.damage_taken_sources[1]["amount"] == 8000
        assert result.damage_taken_sources[2]["amount"] == 5000


# ============================================================
# Orchestrator Tests (compare_mplus_run pipeline)
# ============================================================


class TestCompareOrchestrator:
    """编排器测试 — 段落对齐、汇总构建、无 benchmark 处理。"""

    def test_align_segments_matching(self):
        """3 个玩家段落 + 3 个 benchmark 段落，全部按 position 对齐。"""
        from src.tools.mplus_comparison import _align_segments

        player_segs = [
            {"position": 0, "segment_type": "trash", "name": "Trash #1"},
            {"position": 1, "segment_type": "boss", "name": "Boss #1"},
            {"position": 2, "segment_type": "trash", "name": "Trash #2"},
        ]
        bench_segs = [
            MplusBenchmarkSegment(position=0, segment_type="trash", segment_name="Trash #1"),
            MplusBenchmarkSegment(position=1, segment_type="boss", segment_name="Boss #1"),
            MplusBenchmarkSegment(position=2, segment_type="trash", segment_name="Trash #2"),
        ]
        aligned = _align_segments(player_segs, bench_segs)
        assert len(aligned) == 3
        for p_seg, b_seg in aligned:
            assert b_seg is not None
            assert p_seg["position"] == b_seg.position

    def test_align_segments_mismatch(self):
        """玩家有 4 个段落，benchmark 只有 3 个，多余段落配对 None。"""
        from src.tools.mplus_comparison import _align_segments

        player_segs = [
            {"position": 0, "segment_type": "trash", "name": "Trash #1"},
            {"position": 1, "segment_type": "boss", "name": "Boss #1"},
            {"position": 2, "segment_type": "trash", "name": "Trash #2"},
            {"position": 3, "segment_type": "boss", "name": "Boss #2"},
        ]
        bench_segs = [
            MplusBenchmarkSegment(position=0, segment_type="trash", segment_name="Trash #1"),
            MplusBenchmarkSegment(position=1, segment_type="boss", segment_name="Boss #1"),
            MplusBenchmarkSegment(position=2, segment_type="trash", segment_name="Trash #2"),
        ]
        aligned = _align_segments(player_segs, bench_segs)
        assert len(aligned) == 4
        # 前 3 个有 benchmark
        for i in range(3):
            assert aligned[i][1] is not None
        # 第 4 个无 benchmark
        assert aligned[3][1] is None

    def test_build_summary_counts_flags(self):
        """汇总正确统计 damage/CD/interrupt/death flag 数量。"""
        from src.tools.mplus_comparison import _build_summary

        seg_comps = [
            SegmentComparison(
                position=0, segment_type="trash", segment_name="Trash #1",
                status="compared",
                damage_gaps=[
                    SegmentDamageGap(spell_name="A", spell_id=1, flagged=True),
                    SegmentDamageGap(spell_name="B", spell_id=2, flagged=False),
                ],
                cd_gaps=[
                    {"spell_id": 10, "flagged": True},
                    {"spell_id": 11, "flagged": True},
                ],
                interrupt_comparison={"count_flagged": True},
            ),
            SegmentComparison(
                position=2, segment_type="trash", segment_name="Trash #2",
                status="compared",
                damage_gaps=[
                    SegmentDamageGap(spell_name="C", spell_id=3, flagged=True),
                ],
                cd_gaps=[],
                interrupt_comparison={"count_flagged": False},
            ),
        ]
        summary = _build_summary(seg_comps, [], [DeathBreakdown(), DeathBreakdown()], {})
        assert summary["total_damage_flags"] == 2  # A + C
        assert summary["total_cd_flags"] == 2  # 10 + 11
        assert summary["total_deaths"] == 2
        assert summary["total_interrupt_flags"] == 1
        # worst segment: Trash #1 有 4 flags，Trash #2 有 1 flag
        assert len(summary["worst_segments"]) == 2
        assert summary["worst_segments"][0]["name"] == "Trash #1"

    def test_segment_comparison_no_benchmark(self):
        """无 benchmark 的段落标记 status="no_benchmark"。"""
        from src.tools.mplus_comparison import _build_segment_comparison

        player_data = {
            "position": 5,
            "segment_type": "trash",
            "name": "Trash #3",
            "damage_breakdown": [],
            "cd_casts": [],
            "interrupt_count": 0,
            "interrupt_target_ids": set(),
        }
        result = _build_segment_comparison(player_data, None)
        assert isinstance(result, SegmentComparison)
        assert result.status == "no_benchmark"
        assert result.damage_gaps == []
        assert result.cd_gaps == []
