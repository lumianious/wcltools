# ============================================================
# M+ Benchmark Aggregation 测试
#
# 覆盖 Phase 9 全部 7 项需求:
#   BENCH-02, BENCH-03, CD-01, CD-02, DMG-01, SURV-01, INT-01
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest

from src.models import (
    MplusBenchmarkResponse,
    MplusBenchmarkSegment,
    SegmentCDCast,
    SegmentDamageBreakdown,
)


# ============================================================
# Model Tests (these should PASS now)
# ============================================================


class TestMplusBenchmarkModels:
    """M+ Benchmark Pydantic 模型创建与字段验证。"""

    def test_segment_damage_breakdown_creation(self):
        """SegmentDamageBreakdown 可以创建并包含正确字段。"""
        d = SegmentDamageBreakdown(
            spell_name="Starsurge",
            spell_id=78674,
            total_damage=500000,
            damage_pct=47.6,
        )
        assert d.spell_name == "Starsurge"
        assert d.damage_pct == 47.6

    def test_segment_cd_cast_creation(self):
        """SegmentCDCast 可以创建并包含正确字段。"""
        c = SegmentCDCast(
            spell_name="Celestial Alignment",
            spell_id=194223,
            cast_count_median=1.0,
            ability_type="dps",
        )
        assert c.ability_type == "dps"

    def test_benchmark_segment_creation(self):
        """MplusBenchmarkSegment 包含所有数据类型。"""
        seg = MplusBenchmarkSegment(
            position=0,
            segment_type="trash",
            segment_name="Trash #1",
            duration_median=30.0,
            damage_breakdown=[
                SegmentDamageBreakdown(spell_name="X", damage_pct=100.0)
            ],
            cd_casts=[
                SegmentCDCast(
                    spell_name="Y", cast_count_median=1.0, ability_type="dps"
                )
            ],
            defensive_cds=[
                SegmentCDCast(
                    spell_name="Z",
                    cast_count_median=0.5,
                    ability_type="defensive",
                )
            ],
            interrupt_count_median=2.0,
        )
        assert seg.position == 0
        assert len(seg.damage_breakdown) == 1
        assert len(seg.cd_casts) == 1
        assert seg.interrupt_count_median == 2.0

    def test_benchmark_response_creation(self):
        """MplusBenchmarkResponse 包含 meta + segments + cd_spacing。"""
        from src.models import MplusBenchmarkMeta

        resp = MplusBenchmarkResponse(
            meta=MplusBenchmarkMeta(
                encounter_id=12345, spec="balance-druid", key_level=10
            ),
            segments=[],
            cd_spacing=[],
        )
        assert resp.meta.encounter_id == 12345
        assert resp.segments == []


# ============================================================
# Pipeline Tests (these should FAIL — functions not yet implemented)
# ============================================================


class TestSegmentAlignment:
    """Boss-bounded segment position assignment. Tests _build_segment_positions."""

    def test_build_segment_positions_basic(self):
        """Fights are assigned boss-bounded positions: trash=0, boss=1, trash=2, etc."""
        from src.tools.mplus_benchmarks import _build_segment_positions
        from tests.fixtures.wcl_responses import MPLUS_REPORT_FIGHTS

        # 过滤掉聚合 fight (encounterID > 0 且 id == 1)
        fights = [
            f
            for f in MPLUS_REPORT_FIGHTS
            if f.get("encounterID", 0) == 0 and f.get("id") != 1
        ]
        segments = _build_segment_positions(
            fights,
            boss_names=["Skarmorak", "Master Machinists", "Void Speaker Eirich"],
        )
        # 预期: trash(pos=0), boss(pos=1), trash(pos=2), boss(pos=3), trash(pos=4), boss(pos=5)
        assert len(segments) >= 6
        assert segments[0]["segment_type"] == "trash"
        assert segments[1]["segment_type"] == "boss"
        assert segments[1]["name"] == "Skarmorak"


class TestSegmentDamageExtraction:
    """DMG-01, BENCH-02: Per-segment spell damage % extraction."""

    @pytest.mark.asyncio
    async def test_segment_damage_breakdown(self):
        """从一个段落的 damage table 提取 spell damage %。"""
        from src.tools.mplus_benchmarks import _extract_segment_damage
        from tests.fixtures.wcl_responses import MPLUS_DAMAGE_TABLE_RESPONSE

        entries = MPLUS_DAMAGE_TABLE_RESPONSE["reportData"]["report"]["table"][
            "data"
        ]["entries"]
        result = _extract_segment_damage(entries, top_n=10)
        assert len(result) == 4
        assert result[0].spell_name == "Starsurge"
        assert result[0].damage_pct > 40.0  # 500k / 1050k ~ 47.6%


class TestCDExtraction:
    """CD-01, CD-02: Major CD usage and spacing across segments."""

    @pytest.mark.asyncio
    async def test_segment_cd_casts(self):
        """从段落 cast events 中提取 major CD 施放。"""
        from src.tools.mplus_benchmarks import _extract_segment_cds
        from tests.fixtures.wcl_responses import MPLUS_CAST_EVENTS_RESPONSE

        events = MPLUS_CAST_EVENTS_RESPONSE["reportData"]["report"]["events"][
            "data"
        ]
        tracked = {
            194223: {
                "name": "Celestial Alignment",
                "cd_seconds": 180,
                "ability_type": "dps",
            }
        }
        offensive, defensive = _extract_segment_cds(events, tracked)
        assert len(offensive) >= 1
        assert offensive[0].spell_name == "Celestial Alignment"

    def test_cd_spacing_pattern(self):
        """CD spacing shows which segments get which CDs across the dungeon."""
        from src.tools.mplus_benchmarks import _compute_cd_spacing

        # Mock: 3 segments, CD appears in segment 0 and 2
        segment_cds = {
            0: [
                SegmentCDCast(
                    spell_name="CA",
                    spell_id=194223,
                    cast_count_median=1.0,
                    ability_type="dps",
                )
            ],
            1: [],
            2: [
                SegmentCDCast(
                    spell_name="CA",
                    spell_id=194223,
                    cast_count_median=1.0,
                    ability_type="dps",
                )
            ],
        }
        spacing = _compute_cd_spacing(segment_cds)
        assert any(s["spell_name"] == "CA" for s in spacing)
        ca = next(s for s in spacing if s["spell_name"] == "CA")
        assert ca["segments"] == [0, 2]


class TestDefensiveExtraction:
    """SURV-01: Defensive CD usage patterns."""

    @pytest.mark.asyncio
    async def test_defensive_cd_patterns(self):
        """从段落 cast events 中提取 defensive CD 施放。"""
        from src.tools.mplus_benchmarks import _extract_segment_cds
        from tests.fixtures.wcl_responses import MPLUS_CAST_EVENTS_RESPONSE

        events = MPLUS_CAST_EVENTS_RESPONSE["reportData"]["report"]["events"][
            "data"
        ]
        tracked = {
            22812: {
                "name": "Barkskin",
                "cd_seconds": 60,
                "ability_type": "defensive",
            }
        }
        offensive, defensive = _extract_segment_cds(events, tracked)
        assert len(defensive) >= 1
        assert defensive[0].spell_name == "Barkskin"
        assert defensive[0].ability_type == "defensive"


class TestInterruptExtraction:
    """INT-01: Interrupt count per segment."""

    @pytest.mark.asyncio
    async def test_interrupt_counts(self):
        """从段落 interrupt events 统计打断次数。"""
        from src.tools.mplus_benchmarks import _count_segment_interrupts
        from tests.fixtures.wcl_responses import MPLUS_INTERRUPT_EVENTS_RESPONSE

        events = MPLUS_INTERRUPT_EVENTS_RESPONSE["reportData"]["report"][
            "events"
        ]["data"]
        count = _count_segment_interrupts(events)
        assert count == 2


class TestBossBenchmarks:
    """BENCH-03: Cast-level boss benchmarks."""

    @pytest.mark.asyncio
    async def test_boss_cast_benchmarks(self):
        """Boss 段落提取 cast-level 基准数据（复用 raid 工具模式）。"""
        from src.tools.mplus_benchmarks import _extract_boss_benchmark

        # 将在 Plan 03 完整实现管道集成
        pytest.skip("Implemented in Plan 03 with full pipeline integration")
