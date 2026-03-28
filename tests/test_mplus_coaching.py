# ============================================================
# M+ Coaching Tool 测试
#
# 覆盖 Phase 11 需求:
#   COACH-01 (per-trash / per-boss coaching)
#   COACH-02 (whole-dungeon summary)
#   COACH-03 (dual structured + NL format)
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations


# ============================================================
# Trash Segment Coaching Tests (COACH-01 trash)
# ============================================================


class TestCoachTrashSegment:
    """Trash 段落教练 — top 3 差距 + 正面反馈。"""

    def test_flagged_damage_gaps_sorted_top3(self):
        """有 flagged damage gaps 时返回 top 3，按 gap_pct 降序排列。"""
        from src.models import SegmentComparison, SegmentDamageGap
        from src.tools.mplus_coaching import _coach_trash_segment

        seg = SegmentComparison(
            position=0,
            segment_type="trash",
            segment_name="Trash #1",
            status="compared",
            damage_gaps=[
                SegmentDamageGap(spell_name="Starfire", spell_id=194153, player_pct=10.0, benchmark_pct=40.0, gap_pct=30.0, flagged=True),
                SegmentDamageGap(spell_name="Starsurge", spell_id=78674, player_pct=15.0, benchmark_pct=55.0, gap_pct=40.0, flagged=True),
                SegmentDamageGap(spell_name="Moonfire", spell_id=8921, player_pct=5.0, benchmark_pct=30.0, gap_pct=25.0, flagged=True),
                SegmentDamageGap(spell_name="Sunfire", spell_id=93402, player_pct=20.0, benchmark_pct=25.0, gap_pct=5.0, flagged=False),
                SegmentDamageGap(spell_name="Wrath", spell_id=190984, player_pct=8.0, benchmark_pct=30.0, gap_pct=22.0, flagged=True),
            ],
        )
        result = _coach_trash_segment(seg)
        assert result.position == 0
        assert result.segment_type == "trash"
        assert result.segment_name == "Trash #1"
        # 最多 3 个 items
        assert len(result.items) <= 3
        # 按 gap_pct 降序: Starsurge(40), Starfire(30), Moonfire(25)
        assert result.items[0].spell_name == "Starsurge"
        assert result.items[1].spell_name == "Starfire"
        assert result.items[2].spell_name == "Moonfire"
        # 每个 item 有 coaching_text
        for item in result.items:
            assert item.coaching_text != ""
            assert item.category in ("damage", "cooldown", "interrupt")

    def test_no_flagged_gaps_positive_feedback(self):
        """无 flagged gaps 时返回 positive feedback item。"""
        from src.models import SegmentComparison, SegmentDamageGap
        from src.tools.mplus_coaching import _coach_trash_segment

        seg = SegmentComparison(
            position=1,
            segment_type="trash",
            segment_name="Trash #2",
            status="compared",
            damage_gaps=[
                SegmentDamageGap(spell_name="Starfire", spell_id=194153, player_pct=35.0, benchmark_pct=40.0, gap_pct=5.0, flagged=False),
            ],
            cd_gaps=[],
            interrupt_comparison={"count_flagged": False},
        )
        result = _coach_trash_segment(seg)
        assert len(result.items) == 1
        assert result.items[0].category == "positive"
        assert result.items[0].coaching_text != ""


# ============================================================
# Boss Segment Coaching Tests (COACH-01 boss)
# ============================================================


class TestCoachBossSegment:
    """Boss 段落教练 — top 3 cast/CD issues。"""

    def test_boss_cast_and_cd_gaps(self):
        """有 cast_gaps 和 cd_gaps 时返回 top 3 priority issues。"""
        from src.models import BossCastComparison
        from src.tools.mplus_coaching import _coach_boss_segment

        boss = BossCastComparison(
            boss_name="Atal'ai Coilskin",
            position=1,
            player_duration_sec=120.0,
            benchmark_duration_sec=100.0,
            cast_gaps=[
                {"spell_name": "Starsurge", "spell_id": 78674, "player_count": 5, "bench_count": 10, "gap_pct": 50.0, "flagged": True},
                {"spell_name": "Starfire", "spell_id": 194153, "player_count": 20, "bench_count": 30, "gap_pct": 33.3, "flagged": True},
                {"spell_name": "Wrath", "spell_id": 190984, "player_count": 15, "bench_count": 18, "gap_pct": 16.7, "flagged": False},
            ],
            cd_gaps=[
                {"spell_name": "Incarnation", "spell_id": 102560, "player_casts": 1, "benchmark_median": 2.0, "gap_pct": 50.0, "flagged": True},
            ],
            status="compared",
        )
        result = _coach_boss_segment(boss)
        assert result.position == 1
        assert result.segment_type == "boss"
        assert result.segment_name == "Atal'ai Coilskin"
        assert len(result.items) <= 3
        # 每个 item 有 coaching_text
        for item in result.items:
            assert item.coaching_text != ""
            assert item.category in ("cast", "cooldown")

    def test_boss_no_gaps_positive_feedback(self):
        """Boss 无 flagged 项时返回正面反馈。"""
        from src.models import BossCastComparison
        from src.tools.mplus_coaching import _coach_boss_segment

        boss = BossCastComparison(
            boss_name="Rashanan",
            position=3,
            player_duration_sec=90.0,
            benchmark_duration_sec=95.0,
            cast_gaps=[],
            cd_gaps=[],
            status="compared",
        )
        result = _coach_boss_segment(boss)
        assert len(result.items) == 1
        assert result.items[0].category == "positive"
        assert result.items[0].coaching_text != ""


# ============================================================
# Dungeon Summary Tests (COACH-02)
# ============================================================


class TestBuildDungeonSummary:
    """整个副本教练汇总 — flag 计数 + top 5 改进区域。"""

    def test_summary_stats_and_top_improvements(self):
        """汇总包含 flag 计数和 top improvements，排序正确。"""
        from src.models import CoachingItem, SegmentCoaching
        from src.tools.mplus_coaching import _build_dungeon_summary

        seg_coaching = [
            SegmentCoaching(position=0, segment_type="trash", segment_name="Trash #1", items=[
                CoachingItem(category="damage", spell_name="Starsurge", gap_pct=40.0, coaching_text="x"),
                CoachingItem(category="damage", spell_name="Starfire", gap_pct=30.0, coaching_text="y"),
            ]),
            SegmentCoaching(position=2, segment_type="trash", segment_name="Trash #2", items=[
                CoachingItem(category="cooldown", spell_name="Incarnation", gap_pct=50.0, coaching_text="z"),
            ]),
            SegmentCoaching(position=4, segment_type="trash", segment_name="Trash #3", items=[
                CoachingItem(category="positive", spell_name="", gap_pct=0.0, coaching_text="Good job!"),
            ]),
        ]
        death_coaching = [
            CoachingItem(category="death", spell_name="", gap_pct=0.0, coaching_text="Died at 120s"),
        ]
        comparison_summary = {
            "total_damage_flags": 3,
            "total_cd_flags": 1,
            "total_deaths": 1,
            "total_interrupt_flags": 0,
        }
        result = _build_dungeon_summary(seg_coaching, death_coaching, comparison_summary)
        assert result.total_damage_flags == 3
        assert result.total_cd_flags == 1
        assert result.total_deaths == 1
        assert result.total_interrupt_flags == 0
        # top_improvements: 排除 positive，按 gap_pct 降序
        assert len(result.top_improvements) <= 5
        assert result.top_improvements[0].gap_pct == 50.0  # Incarnation
        assert result.top_improvements[1].gap_pct == 40.0  # Starsurge
        assert result.top_improvements[2].gap_pct == 30.0  # Starfire
        # overall_coaching_text 非空
        assert result.overall_coaching_text != ""


# ============================================================
# NL Advice Generation Tests (COACH-03)
# ============================================================


class TestGenerateTrashAdvice:
    """Trash 段落自然语言建议 — 包含技能名和 benchmark 值。"""

    def test_damage_advice_contains_spell_and_benchmark(self):
        """NL 建议包含技能名和 benchmark 百分比。"""
        from src.models import SegmentDamageGap
        from src.tools.mplus_coaching import _generate_trash_advice

        gap = SegmentDamageGap(
            spell_name="Starsurge", spell_id=78674,
            player_pct=15.0, benchmark_pct=45.0, gap_pct=30.0, flagged=True,
        )
        text = _generate_trash_advice(gap, "Trash #1")
        assert "Starsurge" in text
        assert "Trash #1" in text
        assert "45" in text or "45.0" in text  # benchmark 值


class TestGenerateBossAdvice:
    """Boss 段落自然语言建议 — 包含施法次数差距。"""

    def test_boss_advice_contains_cast_gap_info(self):
        """NL 建议包含施法次数对比信息。"""
        from src.tools.mplus_coaching import _generate_boss_advice

        gap = {
            "spell_name": "Starsurge",
            "spell_id": 78674,
            "player_count": 5,
            "bench_count": 10,
            "gap_pct": 50.0,
            "flagged": True,
        }
        text = _generate_boss_advice(gap, "Rashanan")
        assert "Starsurge" in text
        assert "Rashanan" in text
        assert "5" in text
        assert "10" in text


# ============================================================
# Death Coaching Tests (COACH-03 survival)
# ============================================================


class TestCoachDeaths:
    """死亡教练 — 包含防御技能可用性信息。"""

    def test_death_coaching_with_defensive_info(self):
        """每次死亡生成 NL 建议，包含 segment 和 defensive 信息。"""
        from src.models import DeathBreakdown
        from src.tools.mplus_coaching import _coach_deaths

        deaths = [
            DeathBreakdown(
                death_time_sec=120.5,
                segment_position=2,
                segment_name="Trash #2",
                damage_taken_sources=[
                    {"spell_name": "Shadow Bolt", "amount": 50000},
                    {"spell_name": "Melee", "amount": 20000},
                ],
                defensive_status=[
                    {"spell_name": "Barkskin", "spell_id": 22812, "status": "available_never_used"},
                    {"spell_name": "Renewal", "spell_id": 108238, "status": "on_cooldown"},
                ],
            ),
        ]
        result = _coach_deaths(deaths)
        assert len(result) == 1
        item = result[0]
        assert item.category == "death"
        assert "120" in item.coaching_text  # 死亡时间
        assert "Trash #2" in item.coaching_text  # 段落名
        assert "Shadow Bolt" in item.coaching_text or "Barkskin" in item.coaching_text


# ============================================================
# Full Pipeline Test (COACH-01 + COACH-02 + COACH-03)
# ============================================================


class TestFullPipeline:
    """完整管道: MplusComparisonResponse -> MplusCoachingResponse。"""

    def test_comparison_to_coaching_full_pipeline(self):
        """完整数据产生 segment_coaching + death_coaching + summary。"""
        from src.models import (
            BossCastComparison,
            DeathBreakdown,
            MplusComparisonResponse,
            SegmentComparison,
            SegmentDamageGap,
        )
        from src.tools.mplus_coaching import _build_coaching_response

        comparison = MplusComparisonResponse(
            report_code="abc123",
            player_name="TestPlayer",
            spec="Balance Druid",
            dungeon_name="City of Threads",
            key_level=12,
            benchmark_key_level=12,
            segment_comparisons=[
                SegmentComparison(
                    position=0, segment_type="trash", segment_name="Trash #1",
                    status="compared",
                    damage_gaps=[
                        SegmentDamageGap(spell_name="Starsurge", spell_id=78674, player_pct=10.0, benchmark_pct=45.0, gap_pct=35.0, flagged=True),
                    ],
                    cd_gaps=[
                        {"spell_name": "Incarnation", "spell_id": 102560, "player_casts": 0.0, "benchmark_median": 1.0, "gap_pct": 100.0, "flagged": True},
                    ],
                    interrupt_comparison={"count_flagged": False},
                ),
            ],
            boss_comparisons=[
                BossCastComparison(
                    boss_name="Orator Krix'vizk",
                    position=1,
                    player_duration_sec=120.0,
                    benchmark_duration_sec=100.0,
                    cast_gaps=[
                        {"spell_name": "Starsurge", "spell_id": 78674, "player_count": 5, "bench_count": 10, "gap_pct": 50.0, "flagged": True},
                    ],
                    cd_gaps=[],
                    status="compared",
                ),
            ],
            death_analysis=[
                DeathBreakdown(
                    death_time_sec=60.0,
                    segment_position=0,
                    segment_name="Trash #1",
                    damage_taken_sources=[{"spell_name": "Shadow Bolt", "amount": 50000}],
                    defensive_status=[{"spell_name": "Barkskin", "spell_id": 22812, "status": "available_never_used"}],
                ),
            ],
            summary={
                "total_damage_flags": 1,
                "total_cd_flags": 1,
                "total_deaths": 1,
                "total_interrupt_flags": 0,
                "worst_segments": [{"position": 0, "name": "Trash #1", "flag_count": 2}],
            },
        )
        result = _build_coaching_response(comparison)
        # 基本字段
        assert result.report_code == "abc123"
        assert result.player_name == "TestPlayer"
        assert result.spec == "Balance Druid"
        assert result.dungeon_name == "City of Threads"
        assert result.key_level == 12
        # segment_coaching: 1 trash + 1 boss
        assert len(result.segment_coaching) == 2
        # death_coaching
        assert len(result.death_coaching) == 1
        assert result.death_coaching[0].category == "death"
        # summary
        assert result.summary.total_damage_flags == 1
        assert result.summary.total_cd_flags == 1
        assert result.summary.total_deaths == 1
        assert result.summary.overall_coaching_text != ""
        # 所有 coaching items 有 coaching_text (COACH-03 dual format)
        for seg in result.segment_coaching:
            for item in seg.items:
                assert item.coaching_text != ""
