# ============================================================
# Phase 6D: CD 窗口输出分析测试
# 覆盖模型验证、输出判定逻辑、集成测试
#
# 测试策略:
#   - 纯单元测试: 输出 verdict 判定（不依赖实现）
#   - 模型测试: CDWindowThroughput 验证
#   - 集成测试: PlayerAnalysisResponse 包含 cd_throughput 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    CDWindowThroughput,
    PlayerAnalysisResponse,
)


# ============================================================
# 辅助函数 — CD 窗口输出判定（纯逻辑，镜像 analyze 模块预期行为）
# ============================================================
def _throughput_verdict(
    player_damage: float, benchmark_median_damage: float
) -> str:
    """
    根据玩家伤害与基准中位数判定输出表现。

    - 超过基准 -> "strong"
    - 50%-100% 基准 -> "average"
    - 低于 50% 基准 -> "weak"
    - 基准为 0 -> "strong" (无基准数据时不判定为弱)
    """
    if benchmark_median_damage <= 0:
        return "strong"
    ratio = player_damage / benchmark_median_damage
    if ratio >= 1.0:
        return "strong"
    if ratio >= 0.5:
        return "average"
    return "weak"


# ============================================================
# 模型测试 — CDWindowThroughput
# ============================================================
class TestCDWindowThroughputModel:
    """CDWindowThroughput 数据模型验证。"""

    def test_valid_construction(self):
        """有效 CD 窗口输出数据通过验证"""
        tp = CDWindowThroughput(
            ability_name="Pillar of Frost",
            window_index=0,
            damage_done=2_500_000.0,
            casts_during=8,
            active_time_pct=95.0,
            benchmark_median_damage=2_800_000.0,
            benchmark_median_casts=9.0,
            verdict="average",
        )
        assert tp.ability_name == "Pillar of Frost"
        assert tp.window_index == 0
        assert tp.damage_done == 2_500_000.0
        assert tp.casts_during == 8
        assert tp.active_time_pct == 95.0
        assert tp.benchmark_median_damage == 2_800_000.0
        assert tp.benchmark_median_casts == 9.0
        assert tp.verdict == "average"

    def test_missing_ability_name_raises(self):
        """缺少 ability_name 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                window_index=0,
                damage_done=2_500_000.0,
                casts_during=8,
                active_time_pct=95.0,
                benchmark_median_damage=2_800_000.0,
            )  # type: ignore

    def test_missing_window_index_raises(self):
        """缺少 window_index 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                ability_name="Pillar of Frost",
                damage_done=2_500_000.0,
                casts_during=8,
                active_time_pct=95.0,
                benchmark_median_damage=2_800_000.0,
            )  # type: ignore

    def test_missing_damage_done_raises(self):
        """缺少 damage_done 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                ability_name="Pillar of Frost",
                window_index=0,
                casts_during=8,
                active_time_pct=95.0,
                benchmark_median_damage=2_800_000.0,
            )  # type: ignore

    def test_missing_casts_during_raises(self):
        """缺少 casts_during 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                ability_name="Pillar of Frost",
                window_index=0,
                damage_done=2_500_000.0,
                active_time_pct=95.0,
                benchmark_median_damage=2_800_000.0,
            )  # type: ignore

    def test_missing_active_time_pct_raises(self):
        """缺少 active_time_pct 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                ability_name="Pillar of Frost",
                window_index=0,
                damage_done=2_500_000.0,
                casts_during=8,
                benchmark_median_damage=2_800_000.0,
            )  # type: ignore

    def test_missing_benchmark_median_damage_raises(self):
        """缺少 benchmark_median_damage 被拒绝"""
        with pytest.raises(ValidationError):
            CDWindowThroughput(
                ability_name="Pillar of Frost",
                window_index=0,
                damage_done=2_500_000.0,
                casts_during=8,
                active_time_pct=95.0,
            )  # type: ignore

    def test_verdict_defaults_to_empty(self):
        """verdict 默认为空字符串"""
        tp = CDWindowThroughput(
            ability_name="Pillar of Frost",
            window_index=0,
            damage_done=2_500_000.0,
            casts_during=8,
            active_time_pct=95.0,
            benchmark_median_damage=2_800_000.0,
        )
        assert tp.verdict == ""

    def test_benchmark_median_casts_defaults(self):
        """benchmark_median_casts 默认为 0.0"""
        tp = CDWindowThroughput(
            ability_name="Pillar of Frost",
            window_index=0,
            damage_done=2_500_000.0,
            casts_during=8,
            active_time_pct=95.0,
            benchmark_median_damage=2_800_000.0,
        )
        assert tp.benchmark_median_casts == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CDWindowThroughput(
            ability_name="Pillar of Frost",
            window_index=1,
            damage_done=3_000_000.0,
            casts_during=10,
            active_time_pct=98.0,
            benchmark_median_damage=2_800_000.0,
            benchmark_median_casts=9.0,
            verdict="strong",
        )
        data = original.model_dump()
        rebuilt = CDWindowThroughput(**data)
        assert rebuilt.ability_name == original.ability_name
        assert rebuilt.window_index == original.window_index
        assert rebuilt.damage_done == original.damage_done
        assert rebuilt.casts_during == original.casts_during
        assert rebuilt.active_time_pct == original.active_time_pct
        assert rebuilt.benchmark_median_damage == original.benchmark_median_damage
        assert rebuilt.benchmark_median_casts == original.benchmark_median_casts
        assert rebuilt.verdict == original.verdict


# ============================================================
# 单元测试 — CD 窗口输出 verdict 判定
# ============================================================
class TestThroughputVerdict:
    """CD 窗口输出 verdict 判定逻辑。"""

    def test_above_benchmark_is_strong(self):
        """玩家伤害 > 基准 -> strong"""
        assert _throughput_verdict(3_000_000, 2_800_000) == "strong"

    def test_equal_benchmark_is_strong(self):
        """玩家伤害 = 基准 -> strong"""
        assert _throughput_verdict(2_800_000, 2_800_000) == "strong"

    def test_above_half_is_average(self):
        """玩家伤害在 50%-100% 基准 -> average"""
        assert _throughput_verdict(1_500_000, 2_800_000) == "average"

    def test_at_half_is_average(self):
        """玩家伤害恰好 50% 基准 -> average"""
        assert _throughput_verdict(1_400_000, 2_800_000) == "average"

    def test_below_half_is_weak(self):
        """玩家伤害 < 50% 基准 -> weak"""
        assert _throughput_verdict(1_000_000, 2_800_000) == "weak"

    def test_zero_damage_is_weak(self):
        """玩家伤害 0 -> weak"""
        assert _throughput_verdict(0, 2_800_000) == "weak"

    def test_zero_benchmark_is_strong(self):
        """基准为 0（无数据）-> strong"""
        assert _throughput_verdict(1_000_000, 0) == "strong"

    def test_negative_benchmark_is_strong(self):
        """基准为负数（异常数据）-> strong"""
        assert _throughput_verdict(1_000_000, -100) == "strong"

    def test_just_below_benchmark(self):
        """略低于基准 -> average"""
        assert _throughput_verdict(2_799_999, 2_800_000) == "average"

    def test_just_above_half(self):
        """略高于 50% -> average"""
        assert _throughput_verdict(1_400_001, 2_800_000) == "average"

    def test_just_below_half(self):
        """略低于 50% -> weak"""
        assert _throughput_verdict(1_399_999, 2_800_000) == "weak"


# ============================================================
# 集成测试 — PlayerAnalysisResponse 包含 cd_throughput 字段
# ============================================================
class TestPlayerAnalysisResponseCDThroughput:
    """PlayerAnalysisResponse 中 cd_throughput 字段集成测试。"""

    def test_with_cd_throughput_populated(self):
        """构造包含 cd_throughput 的 PlayerAnalysisResponse -> 序列化正确"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            cd_throughput=[
                CDWindowThroughput(
                    ability_name="Pillar of Frost",
                    window_index=0,
                    damage_done=2_500_000.0,
                    casts_during=8,
                    active_time_pct=95.0,
                    benchmark_median_damage=2_800_000.0,
                    benchmark_median_casts=9.0,
                    verdict="average",
                ),
                CDWindowThroughput(
                    ability_name="Pillar of Frost",
                    window_index=1,
                    damage_done=3_100_000.0,
                    casts_during=10,
                    active_time_pct=98.0,
                    benchmark_median_damage=2_800_000.0,
                    benchmark_median_casts=9.0,
                    verdict="strong",
                ),
            ],
        )
        assert len(response.cd_throughput) == 2
        assert response.cd_throughput[0].verdict == "average"
        assert response.cd_throughput[1].verdict == "strong"

        data = response.model_dump()
        assert len(data["cd_throughput"]) == 2

    def test_with_cd_throughput_empty(self):
        """cd_throughput 默认为空列表"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.cd_throughput == []
        data = response.model_dump()
        assert data["cd_throughput"] == []

    def test_full_round_trip_with_cd_throughput(self):
        """model_dump -> 重建 -> 完整 cd_throughput 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            cd_throughput=[
                CDWindowThroughput(
                    ability_name="Incarnation: Chosen of Elune",
                    window_index=0,
                    damage_done=5_000_000.0,
                    casts_during=18,
                    active_time_pct=90.0,
                    benchmark_median_damage=5_500_000.0,
                    benchmark_median_casts=20.0,
                    verdict="average",
                ),
            ],
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert len(rebuilt.cd_throughput) == 1
        assert rebuilt.cd_throughput[0].ability_name == "Incarnation: Chosen of Elune"
        assert rebuilt.cd_throughput[0].verdict == "average"

    def test_multiple_abilities(self):
        """多个不同技能的 CD 窗口输出"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            cd_throughput=[
                CDWindowThroughput(
                    ability_name="Incarnation: Chosen of Elune",
                    window_index=0,
                    damage_done=5_000_000.0,
                    casts_during=18,
                    active_time_pct=90.0,
                    benchmark_median_damage=5_500_000.0,
                    verdict="average",
                ),
                CDWindowThroughput(
                    ability_name="Celestial Alignment",
                    window_index=0,
                    damage_done=3_000_000.0,
                    casts_during=12,
                    active_time_pct=85.0,
                    benchmark_median_damage=2_800_000.0,
                    verdict="strong",
                ),
            ],
        )
        abilities = {t.ability_name for t in response.cd_throughput}
        assert "Incarnation: Chosen of Elune" in abilities
        assert "Celestial Alignment" in abilities
