# ============================================================
# Phase 6B: CD 窗口事件关联测试
# 覆盖模型验证、窗口密度计算、低密度判定、集成测试
#
# 测试策略:
#   - 纯单元测试: 窗口密度计算、低密度判定（不依赖实现）
#   - 模型测试: CooldownWindowDetail, EventLinkingAnalysis 验证
#   - 集成测试: PlayerAnalysisResponse 包含 cd_window_analysis 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    CooldownWindowDetail,
    EventLinkingAnalysis,
    PlayerAnalysisResponse,
)


# ============================================================
# 辅助函数 — CD 窗口密度计算（纯逻辑，镜像 analyze 模块预期行为）
# ============================================================
def _calc_window_density(
    casts_during: int, window_duration_sec: float, gcd: float = 1.0
) -> float:
    """
    计算 CD 窗口内的施法密度。

    density = actual_casts / theoretical_max_casts * 100
    theoretical_max = window_duration / gcd
    """
    if window_duration_sec <= 0 or gcd <= 0:
        return 0.0
    theoretical_max = window_duration_sec / gcd
    if theoretical_max <= 0:
        return 0.0
    return casts_during / theoretical_max * 100.0


def _is_low_density(density: float, threshold: float = 70.0) -> bool:
    """判定是否为低密度窗口（密度 < 阈值）。"""
    return density < threshold


# ============================================================
# 模型测试 — CooldownWindowDetail
# ============================================================
class TestCooldownWindowDetailModel:
    """CooldownWindowDetail 数据模型验证。"""

    def test_valid_construction(self):
        """有效 CD 窗口数据通过验证"""
        w = CooldownWindowDetail(
            buff_name="Pillar of Frost",
            buff_spell_id=51271,
            start_sec=10.0,
            end_sec=22.0,
            duration_sec=12.0,
            casts_during=8,
            density_pct=100.0,
        )
        assert w.buff_name == "Pillar of Frost"
        assert w.buff_spell_id == 51271
        assert w.start_sec == 10.0
        assert w.end_sec == 22.0
        assert w.duration_sec == 12.0
        assert w.casts_during == 8
        assert w.density_pct == 100.0

    def test_missing_buff_name_raises(self):
        """缺少 buff_name 被拒绝"""
        with pytest.raises(ValidationError):
            CooldownWindowDetail(
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=8,
                density_pct=100.0,
            )  # type: ignore

    def test_missing_buff_spell_id_raises(self):
        """缺少 buff_spell_id 被拒绝"""
        with pytest.raises(ValidationError):
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=8,
                density_pct=100.0,
            )  # type: ignore

    def test_missing_start_sec_raises(self):
        """缺少 start_sec 被拒绝"""
        with pytest.raises(ValidationError):
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=8,
                density_pct=100.0,
            )  # type: ignore

    def test_missing_casts_during_raises(self):
        """缺少 casts_during 被拒绝"""
        with pytest.raises(ValidationError):
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                density_pct=100.0,
            )  # type: ignore

    def test_missing_density_pct_raises(self):
        """缺少 density_pct 被拒绝"""
        with pytest.raises(ValidationError):
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=8,
            )  # type: ignore

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CooldownWindowDetail(
            buff_name="Pillar of Frost",
            buff_spell_id=51271,
            start_sec=10.0,
            end_sec=22.0,
            duration_sec=12.0,
            casts_during=8,
            density_pct=95.5,
        )
        data = original.model_dump()
        rebuilt = CooldownWindowDetail(**data)
        assert rebuilt.buff_name == original.buff_name
        assert rebuilt.buff_spell_id == original.buff_spell_id
        assert rebuilt.start_sec == original.start_sec
        assert rebuilt.end_sec == original.end_sec
        assert rebuilt.duration_sec == original.duration_sec
        assert rebuilt.casts_during == original.casts_during
        assert rebuilt.density_pct == original.density_pct


# ============================================================
# 模型测试 — EventLinkingAnalysis
# ============================================================
class TestEventLinkingAnalysisModel:
    """EventLinkingAnalysis 数据模型验证。"""

    def test_valid_construction(self):
        """有效事件关联分析数据通过验证"""
        analysis = EventLinkingAnalysis(
            cooldown_windows=[
                CooldownWindowDetail(
                    buff_name="Pillar of Frost",
                    buff_spell_id=51271,
                    start_sec=10.0,
                    end_sec=22.0,
                    duration_sec=12.0,
                    casts_during=8,
                    density_pct=100.0,
                ),
            ],
            low_density_windows_count=0,
            verdict="ok",
        )
        assert len(analysis.cooldown_windows) == 1
        assert analysis.low_density_windows_count == 0
        assert analysis.verdict == "ok"

    def test_empty_cooldown_windows(self):
        """空 cooldown_windows 列表有效"""
        analysis = EventLinkingAnalysis(
            cooldown_windows=[],
            low_density_windows_count=0,
            verdict="ok",
        )
        assert analysis.cooldown_windows == []

    def test_defaults(self):
        """默认值正确"""
        analysis = EventLinkingAnalysis()
        assert analysis.cooldown_windows == []
        assert analysis.low_density_windows_count == 0
        assert analysis.verdict == ""

    def test_low_density_verdict(self):
        """低密度窗口 verdict 构造"""
        analysis = EventLinkingAnalysis(
            cooldown_windows=[
                CooldownWindowDetail(
                    buff_name="Pillar of Frost",
                    buff_spell_id=51271,
                    start_sec=10.0,
                    end_sec=22.0,
                    duration_sec=12.0,
                    casts_during=3,
                    density_pct=37.5,
                ),
            ],
            low_density_windows_count=1,
            verdict="low_density_burst",
        )
        assert analysis.verdict == "low_density_burst"
        assert analysis.low_density_windows_count == 1

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = EventLinkingAnalysis(
            cooldown_windows=[
                CooldownWindowDetail(
                    buff_name="Incarnation",
                    buff_spell_id=102560,
                    start_sec=5.0,
                    end_sec=35.0,
                    duration_sec=30.0,
                    casts_during=18,
                    density_pct=90.0,
                ),
            ],
            low_density_windows_count=0,
            verdict="ok",
        )
        data = original.model_dump()
        rebuilt = EventLinkingAnalysis(**data)
        assert len(rebuilt.cooldown_windows) == 1
        assert rebuilt.cooldown_windows[0].buff_name == "Incarnation"
        assert rebuilt.low_density_windows_count == 0
        assert rebuilt.verdict == "ok"


# ============================================================
# 单元测试 — CD 窗口密度计算
# ============================================================
class TestCalcWindowDensity:
    """CD 窗口施法密度计算逻辑。"""

    def test_full_density(self):
        """满密度窗口: 12 秒 / 1.0 GCD = 12 次, 实际 12 次 -> 100%"""
        density = _calc_window_density(12, 12.0)
        assert abs(density - 100.0) < 0.01

    def test_half_density(self):
        """半密度: 12 秒 / 1.0 = 12 理论, 实际 6 次 -> 50%"""
        density = _calc_window_density(6, 12.0)
        assert abs(density - 50.0) < 0.01

    def test_empty_window(self):
        """空窗口: 0 次施法 -> 0%"""
        density = _calc_window_density(0, 12.0)
        assert density == 0.0

    def test_zero_duration(self):
        """持续时间为 0 -> 0%"""
        density = _calc_window_density(5, 0.0)
        assert density == 0.0

    def test_negative_duration(self):
        """负数持续时间 -> 0%"""
        density = _calc_window_density(5, -1.0)
        assert density == 0.0

    def test_short_window(self):
        """短窗口: 3 秒 / 1.0 = 3 理论, 实际 3 次 -> 100%"""
        density = _calc_window_density(3, 3.0)
        assert abs(density - 100.0) < 0.01

    def test_over_density(self):
        """超密度: 实际施法多于理论（可能包含非 GCD 技能）"""
        density = _calc_window_density(15, 12.0)
        assert density > 100.0

    def test_custom_gcd(self):
        """自定义 GCD: 1.5 秒"""
        # 12 秒 / 1.5 = 8 理论, 实际 4 次 -> 50%
        density = _calc_window_density(4, 12.0, gcd=1.5)
        assert abs(density - 50.0) < 0.01

    def test_zero_gcd(self):
        """GCD 为 0 -> 0%"""
        density = _calc_window_density(5, 12.0, gcd=0.0)
        assert density == 0.0


# ============================================================
# 单元测试 — 低密度判定
# ============================================================
class TestIsLowDensity:
    """低密度窗口判定逻辑。"""

    def test_below_threshold(self):
        """密度 50% < 70% -> 低密度"""
        assert _is_low_density(50.0) is True

    def test_above_threshold(self):
        """密度 90% > 70% -> 非低密度"""
        assert _is_low_density(90.0) is False

    def test_at_threshold(self):
        """密度恰好 70% -> 非低密度（不严格小于）"""
        assert _is_low_density(70.0) is False

    def test_just_below_threshold(self):
        """密度 69.9% -> 低密度"""
        assert _is_low_density(69.9) is True

    def test_zero_density(self):
        """密度 0% -> 低密度"""
        assert _is_low_density(0.0) is True

    def test_full_density(self):
        """密度 100% -> 非低密度"""
        assert _is_low_density(100.0) is False

    def test_custom_threshold(self):
        """自定义阈值 50%"""
        assert _is_low_density(49.0, threshold=50.0) is True
        assert _is_low_density(50.0, threshold=50.0) is False
        assert _is_low_density(51.0, threshold=50.0) is False


# ============================================================
# 集成测试 — PlayerAnalysisResponse 包含 cd_window_analysis 字段
# ============================================================
class TestPlayerAnalysisResponseCDWindows:
    """PlayerAnalysisResponse 中 cd_window_analysis 字段集成测试。"""

    def test_with_cd_window_analysis_populated(self):
        """构造包含 cd_window_analysis 的 PlayerAnalysisResponse -> 序列化正确"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            cd_window_analysis=EventLinkingAnalysis(
                cooldown_windows=[
                    CooldownWindowDetail(
                        buff_name="Pillar of Frost",
                        buff_spell_id=51271,
                        start_sec=10.0,
                        end_sec=22.0,
                        duration_sec=12.0,
                        casts_during=8,
                        density_pct=100.0,
                    ),
                    CooldownWindowDetail(
                        buff_name="Pillar of Frost",
                        buff_spell_id=51271,
                        start_sec=75.0,
                        end_sec=87.0,
                        duration_sec=12.0,
                        casts_during=4,
                        density_pct=50.0,
                    ),
                ],
                low_density_windows_count=1,
                verdict="low_density_burst",
            ),
        )
        assert response.cd_window_analysis is not None
        assert len(response.cd_window_analysis.cooldown_windows) == 2
        assert response.cd_window_analysis.low_density_windows_count == 1

        # 序列化验证
        data = response.model_dump()
        assert data["cd_window_analysis"]["low_density_windows_count"] == 1
        assert len(data["cd_window_analysis"]["cooldown_windows"]) == 2

    def test_with_cd_window_analysis_none(self):
        """cd_window_analysis=None -> 可选字段，序列化为 None"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.cd_window_analysis is None
        data = response.model_dump()
        assert data["cd_window_analysis"] is None

    def test_full_round_trip_with_cd_windows(self):
        """model_dump -> 重建 -> 完整 cd_window_analysis 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            cd_window_analysis=EventLinkingAnalysis(
                cooldown_windows=[
                    CooldownWindowDetail(
                        buff_name="Pillar of Frost",
                        buff_spell_id=51271,
                        start_sec=10.0,
                        end_sec=22.0,
                        duration_sec=12.0,
                        casts_during=8,
                        density_pct=100.0,
                    ),
                ],
                low_density_windows_count=0,
                verdict="ok",
            ),
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.cd_window_analysis is not None
        assert len(rebuilt.cd_window_analysis.cooldown_windows) == 1
        assert rebuilt.cd_window_analysis.verdict == "ok"


# ============================================================
# 单元测试 — top_issues 中的 CD 窗口低密度标记
# ============================================================
class TestCDWindowTopIssues:
    """CD 窗口分析对 top_issues 的影响。"""

    def test_low_density_triggers_issue(self):
        """低密度窗口 -> 触发 top_issues 条目"""
        windows = [
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=3,
                density_pct=37.5,
            ),
        ]
        # 模拟 top_issues 生成逻辑
        issues: list[str] = []
        low_count = 0
        for w in windows:
            if _is_low_density(w.density_pct):
                low_count += 1
        if low_count > 0:
            issues.append(f"CD 窗口施法密度偏低 ({low_count} 个窗口)")
        assert len(issues) == 1
        assert "CD 窗口施法密度偏低" in issues[0]

    def test_ok_does_not_trigger_issue(self):
        """正常密度窗口 -> 不产生 top_issues 条目"""
        windows = [
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=8,
                density_pct=100.0,
            ),
        ]
        issues: list[str] = []
        low_count = 0
        for w in windows:
            if _is_low_density(w.density_pct):
                low_count += 1
        if low_count > 0:
            issues.append("CD 窗口施法密度偏低")
        assert issues == []

    def test_multiple_low_density_windows(self):
        """多个低密度窗口 -> 计数正确"""
        windows = [
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=10.0,
                end_sec=22.0,
                duration_sec=12.0,
                casts_during=3,
                density_pct=37.5,
            ),
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=75.0,
                end_sec=87.0,
                duration_sec=12.0,
                casts_during=2,
                density_pct=25.0,
            ),
            CooldownWindowDetail(
                buff_name="Pillar of Frost",
                buff_spell_id=51271,
                start_sec=140.0,
                end_sec=152.0,
                duration_sec=12.0,
                casts_during=7,
                density_pct=87.5,
            ),
        ]
        low_count = sum(1 for w in windows if _is_low_density(w.density_pct))
        assert low_count == 2
