# ============================================================
# Phase 6C: 天赋技能使用分析测试
# 覆盖模型验证、天赋使用判定逻辑、集成测试
#
# 测试策略:
#   - 纯单元测试: 天赋使用 verdict 判定（不依赖实现）
#   - 模型测试: TalentUsageGap, TalentUsageAnalysis 验证
#   - 集成测试: PlayerAnalysisResponse 包含 talent_usage 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    PlayerAnalysisResponse,
    TalentUsageAnalysis,
    TalentUsageGap,
)


# ============================================================
# 辅助函数 — 天赋使用判定（纯逻辑，镜像 analyze 模块预期行为）
# ============================================================
def _talent_usage_verdict(
    player_casts: int, benchmark_median: float
) -> str:
    """
    根据玩家施法次数与基准中位数判定天赋使用情况。

    - 0 次施法 -> "unused"
    - 少于基准 p25 (近似 median * 0.5) -> "underused"
    - 其他 -> "ok"
    """
    if player_casts == 0:
        return "unused"
    if benchmark_median <= 0:
        return "ok"
    # 近似 p25 为 median 的 50%
    p25_approx = benchmark_median * 0.5
    if player_casts < p25_approx:
        return "underused"
    return "ok"


def _collect_unused_talent_spells(
    talent_gaps: list[dict],
) -> list[str]:
    """从天赋差距列表中收集未使用的天赋技能名。"""
    return [g["spell_name"] for g in talent_gaps if g["verdict"] == "unused"]


# ============================================================
# 模型测试 — TalentUsageGap
# ============================================================
class TestTalentUsageGapModel:
    """TalentUsageGap 数据模型验证。"""

    def test_valid_construction(self):
        """有效天赋使用差距数据通过验证"""
        gap = TalentUsageGap(
            talent_name="Obliteration",
            talent_id=1001,
            spell_name="Obliterate",
            spell_id=49020,
            player_casts=15,
            benchmark_median_casts=25.0,
            player_cpm=3.0,
            benchmark_cpm=5.0,
            verdict="underused",
        )
        assert gap.talent_name == "Obliteration"
        assert gap.talent_id == 1001
        assert gap.spell_name == "Obliterate"
        assert gap.spell_id == 49020
        assert gap.player_casts == 15
        assert gap.benchmark_median_casts == 25.0
        assert gap.player_cpm == 3.0
        assert gap.benchmark_cpm == 5.0
        assert gap.verdict == "underused"

    def test_missing_talent_name_raises(self):
        """缺少 talent_name 被拒绝"""
        with pytest.raises(ValidationError):
            TalentUsageGap(
                talent_id=1001,
                spell_name="Obliterate",
                spell_id=49020,
                player_casts=15,
                benchmark_median_casts=25.0,
                player_cpm=3.0,
                benchmark_cpm=5.0,
            )  # type: ignore

    def test_missing_spell_id_raises(self):
        """缺少 spell_id 被拒绝"""
        with pytest.raises(ValidationError):
            TalentUsageGap(
                talent_name="Obliteration",
                talent_id=1001,
                spell_name="Obliterate",
                player_casts=15,
                benchmark_median_casts=25.0,
                player_cpm=3.0,
                benchmark_cpm=5.0,
            )  # type: ignore

    def test_missing_player_casts_raises(self):
        """缺少 player_casts 被拒绝"""
        with pytest.raises(ValidationError):
            TalentUsageGap(
                talent_name="Obliteration",
                talent_id=1001,
                spell_name="Obliterate",
                spell_id=49020,
                benchmark_median_casts=25.0,
                player_cpm=3.0,
                benchmark_cpm=5.0,
            )  # type: ignore

    def test_missing_benchmark_median_casts_raises(self):
        """缺少 benchmark_median_casts 被拒绝"""
        with pytest.raises(ValidationError):
            TalentUsageGap(
                talent_name="Obliteration",
                talent_id=1001,
                spell_name="Obliterate",
                spell_id=49020,
                player_casts=15,
                player_cpm=3.0,
                benchmark_cpm=5.0,
            )  # type: ignore

    def test_verdict_defaults_to_empty(self):
        """verdict 默认为空字符串"""
        gap = TalentUsageGap(
            talent_name="Obliteration",
            talent_id=1001,
            spell_name="Obliterate",
            spell_id=49020,
            player_casts=15,
            benchmark_median_casts=25.0,
            player_cpm=3.0,
            benchmark_cpm=5.0,
        )
        assert gap.verdict == ""

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = TalentUsageGap(
            talent_name="Obliteration",
            talent_id=1001,
            spell_name="Obliterate",
            spell_id=49020,
            player_casts=15,
            benchmark_median_casts=25.0,
            player_cpm=3.0,
            benchmark_cpm=5.0,
            verdict="underused",
        )
        data = original.model_dump()
        rebuilt = TalentUsageGap(**data)
        assert rebuilt.talent_name == original.talent_name
        assert rebuilt.talent_id == original.talent_id
        assert rebuilt.spell_name == original.spell_name
        assert rebuilt.spell_id == original.spell_id
        assert rebuilt.player_casts == original.player_casts
        assert rebuilt.benchmark_median_casts == original.benchmark_median_casts
        assert rebuilt.verdict == original.verdict


# ============================================================
# 模型测试 — TalentUsageAnalysis
# ============================================================
class TestTalentUsageAnalysisModel:
    """TalentUsageAnalysis 数据模型验证。"""

    def test_valid_construction(self):
        """有效天赋使用分析数据通过验证"""
        analysis = TalentUsageAnalysis(
            talent_gaps=[
                TalentUsageGap(
                    talent_name="Obliteration",
                    talent_id=1001,
                    spell_name="Obliterate",
                    spell_id=49020,
                    player_casts=0,
                    benchmark_median_casts=25.0,
                    player_cpm=0.0,
                    benchmark_cpm=5.0,
                    verdict="unused",
                ),
            ],
            unused_talent_spells=["Obliterate"],
        )
        assert len(analysis.talent_gaps) == 1
        assert analysis.unused_talent_spells == ["Obliterate"]

    def test_defaults(self):
        """默认值正确"""
        analysis = TalentUsageAnalysis()
        assert analysis.talent_gaps == []
        assert analysis.unused_talent_spells == []

    def test_empty_talent_gaps(self):
        """空天赋差距列表有效"""
        analysis = TalentUsageAnalysis(talent_gaps=[])
        assert analysis.talent_gaps == []

    def test_multiple_gaps(self):
        """多个天赋差距条目"""
        analysis = TalentUsageAnalysis(
            talent_gaps=[
                TalentUsageGap(
                    talent_name="Obliteration",
                    talent_id=1001,
                    spell_name="Obliterate",
                    spell_id=49020,
                    player_casts=0,
                    benchmark_median_casts=25.0,
                    player_cpm=0.0,
                    benchmark_cpm=5.0,
                    verdict="unused",
                ),
                TalentUsageGap(
                    talent_name="Breath of Sindragosa",
                    talent_id=1002,
                    spell_name="Breath of Sindragosa",
                    spell_id=152279,
                    player_casts=1,
                    benchmark_median_casts=3.0,
                    player_cpm=0.2,
                    benchmark_cpm=0.6,
                    verdict="underused",
                ),
            ],
            unused_talent_spells=["Obliterate"],
        )
        assert len(analysis.talent_gaps) == 2
        assert len(analysis.unused_talent_spells) == 1

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = TalentUsageAnalysis(
            talent_gaps=[
                TalentUsageGap(
                    talent_name="Obliteration",
                    talent_id=1001,
                    spell_name="Obliterate",
                    spell_id=49020,
                    player_casts=20,
                    benchmark_median_casts=25.0,
                    player_cpm=4.0,
                    benchmark_cpm=5.0,
                    verdict="ok",
                ),
            ],
            unused_talent_spells=[],
        )
        data = original.model_dump()
        rebuilt = TalentUsageAnalysis(**data)
        assert len(rebuilt.talent_gaps) == 1
        assert rebuilt.talent_gaps[0].verdict == "ok"
        assert rebuilt.unused_talent_spells == []


# ============================================================
# 单元测试 — 天赋使用 verdict 判定
# ============================================================
class TestTalentUsageVerdict:
    """天赋使用 verdict 判定逻辑。"""

    def test_zero_casts_is_unused(self):
        """0 次施法 -> unused"""
        assert _talent_usage_verdict(0, 25.0) == "unused"

    def test_zero_casts_zero_benchmark_is_unused(self):
        """0 次施法、基准也为 0 -> unused"""
        assert _talent_usage_verdict(0, 0.0) == "unused"

    def test_low_casts_is_underused(self):
        """少于基准 p25 -> underused"""
        # p25 近似 = 25 * 0.5 = 12.5, 玩家 5 次 < 12.5
        assert _talent_usage_verdict(5, 25.0) == "underused"

    def test_adequate_casts_is_ok(self):
        """充足施法次数 -> ok"""
        # p25 近似 = 25 * 0.5 = 12.5, 玩家 15 次 > 12.5
        assert _talent_usage_verdict(15, 25.0) == "ok"

    def test_above_benchmark_is_ok(self):
        """超过基准 -> ok"""
        assert _talent_usage_verdict(30, 25.0) == "ok"

    def test_no_benchmark_data_is_ok(self):
        """基准为 0（无基准数据）-> ok"""
        assert _talent_usage_verdict(5, 0.0) == "ok"

    def test_negative_benchmark_is_ok(self):
        """基准为负数（异常数据）-> ok"""
        assert _talent_usage_verdict(5, -10.0) == "ok"

    def test_at_p25_boundary(self):
        """恰好在 p25 边界 -> ok"""
        # p25 近似 = 20 * 0.5 = 10.0, 玩家 10 次 -> ok (不小于)
        assert _talent_usage_verdict(10, 20.0) == "ok"

    def test_just_below_p25(self):
        """略低于 p25 -> underused"""
        # p25 近似 = 20 * 0.5 = 10.0, 玩家 9 次 < 10.0
        assert _talent_usage_verdict(9, 20.0) == "underused"


# ============================================================
# 单元测试 — 未使用天赋技能列表生成
# ============================================================
class TestCollectUnusedTalentSpells:
    """未使用天赋技能列表生成逻辑。"""

    def test_collect_unused(self):
        """收集所有 unused 天赋技能名"""
        gaps = [
            {"spell_name": "Obliterate", "verdict": "unused"},
            {"spell_name": "Frost Strike", "verdict": "ok"},
            {"spell_name": "Breath of Sindragosa", "verdict": "unused"},
        ]
        unused = _collect_unused_talent_spells(gaps)
        assert unused == ["Obliterate", "Breath of Sindragosa"]

    def test_no_unused(self):
        """全部正常 -> 空列表"""
        gaps = [
            {"spell_name": "Obliterate", "verdict": "ok"},
            {"spell_name": "Frost Strike", "verdict": "ok"},
        ]
        unused = _collect_unused_talent_spells(gaps)
        assert unused == []

    def test_all_unused(self):
        """全部未使用"""
        gaps = [
            {"spell_name": "Obliterate", "verdict": "unused"},
            {"spell_name": "Frost Strike", "verdict": "unused"},
        ]
        unused = _collect_unused_talent_spells(gaps)
        assert len(unused) == 2

    def test_empty_list(self):
        """空列表 -> 空结果"""
        assert _collect_unused_talent_spells([]) == []

    def test_underused_not_collected(self):
        """underused 不在 unused 列表中"""
        gaps = [
            {"spell_name": "Obliterate", "verdict": "underused"},
        ]
        assert _collect_unused_talent_spells(gaps) == []


# ============================================================
# 集成测试 — PlayerAnalysisResponse 包含 talent_usage 字段
# ============================================================
class TestPlayerAnalysisResponseTalentUsage:
    """PlayerAnalysisResponse 中 talent_usage 字段集成测试。"""

    def test_with_talent_usage_populated(self):
        """构造包含 talent_usage 的 PlayerAnalysisResponse -> 序列化正确"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            talent_usage=TalentUsageAnalysis(
                talent_gaps=[
                    TalentUsageGap(
                        talent_name="Obliteration",
                        talent_id=1001,
                        spell_name="Obliterate",
                        spell_id=49020,
                        player_casts=0,
                        benchmark_median_casts=25.0,
                        player_cpm=0.0,
                        benchmark_cpm=5.0,
                        verdict="unused",
                    ),
                ],
                unused_talent_spells=["Obliterate"],
            ),
        )
        assert response.talent_usage is not None
        assert len(response.talent_usage.talent_gaps) == 1
        assert response.talent_usage.unused_talent_spells == ["Obliterate"]

        data = response.model_dump()
        assert len(data["talent_usage"]["talent_gaps"]) == 1
        assert data["talent_usage"]["unused_talent_spells"] == ["Obliterate"]

    def test_with_talent_usage_none(self):
        """talent_usage=None -> 可选字段，序列化为 None"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.talent_usage is None
        data = response.model_dump()
        assert data["talent_usage"] is None

    def test_full_round_trip_with_talent_usage(self):
        """model_dump -> 重建 -> 完整 talent_usage 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            talent_usage=TalentUsageAnalysis(
                talent_gaps=[
                    TalentUsageGap(
                        talent_name="Obliteration",
                        talent_id=1001,
                        spell_name="Obliterate",
                        spell_id=49020,
                        player_casts=20,
                        benchmark_median_casts=25.0,
                        player_cpm=4.0,
                        benchmark_cpm=5.0,
                        verdict="ok",
                    ),
                ],
                unused_talent_spells=[],
            ),
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.talent_usage is not None
        assert len(rebuilt.talent_usage.talent_gaps) == 1
        assert rebuilt.talent_usage.talent_gaps[0].verdict == "ok"


# ============================================================
# 单元测试 — top_issues 中的天赋未使用标记
# ============================================================
class TestTalentUsageTopIssues:
    """天赋使用分析对 top_issues 的影响。"""

    def test_unused_triggers_issue(self):
        """未使用天赋技能 -> 触发 top_issues 条目"""
        unused_spells = ["Obliterate", "Breath of Sindragosa"]
        issues: list[str] = []
        if unused_spells:
            issues.append(
                f"天赋技能未使用: {', '.join(unused_spells)}"
            )
        assert len(issues) == 1
        assert "Obliterate" in issues[0]
        assert "Breath of Sindragosa" in issues[0]

    def test_no_unused_no_issue(self):
        """无未使用天赋 -> 不产生 top_issues"""
        unused_spells: list[str] = []
        issues: list[str] = []
        if unused_spells:
            issues.append("天赋技能未使用")
        assert issues == []

    def test_single_unused_triggers(self):
        """单个未使用天赋 -> 触发 top_issues"""
        unused_spells = ["Starlord"]
        issues: list[str] = []
        if unused_spells:
            issues.append(f"天赋技能未使用: {', '.join(unused_spells)}")
        assert len(issues) == 1
        assert "Starlord" in issues[0]
