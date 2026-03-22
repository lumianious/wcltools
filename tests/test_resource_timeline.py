# ============================================================
# Phase 7: 资源时间线 (ResourceTimeline) 测试
# 覆盖模型验证、溢出检测、封顶时间计算、集成测试
#
# 测试策略:
#   - 模型测试: ResourcePoint, ResourceTimelineResponse 验证
#   - 纯单元测试: 溢出检测、封顶时间计算（不依赖实现）
#   - 集成测试: ResourceTimelineResponse 完整构造与序列化
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    ResourcePoint,
    ResourceTimelineResponse,
)


# ============================================================
# 辅助函数 — 溢出检测（纯逻辑，镜像 resource_timeline 工具预期行为）
# ============================================================
def _detect_overflow(value: int, max_value: int) -> bool:
    """
    检测资源值是否达到上限（溢出）。

    max_value <= 0 表示无上限，不算溢出。
    """
    if max_value <= 0:
        return False
    return value >= max_value


def _calc_time_at_cap(
    points: list[dict],
    fight_duration: float,
) -> float:
    """
    计算资源值处于上限的时间百分比。

    对每个数据点，假设其值持续到下一个数据点。
    """
    if fight_duration <= 0 or not points:
        return 0.0

    capped_time = 0.0
    for i, pt in enumerate(points):
        if pt["max_value"] <= 0:
            continue
        if pt["value"] < pt["max_value"]:
            continue
        # 该点处于上限
        if i + 1 < len(points):
            next_ts = points[i + 1]["timestamp_sec"]
        else:
            next_ts = fight_duration
        duration = next_ts - pt["timestamp_sec"]
        capped_time += duration

    return capped_time / fight_duration * 100.0


# ============================================================
# 模型测试 — ResourcePoint
# ============================================================
class TestResourcePointModel:
    """ResourcePoint 数据模型验证。"""

    def test_valid_construction(self):
        """有效资源值数据通过验证"""
        p = ResourcePoint(
            timestamp_sec=10.5,
            value=80,
            max_value=100,
            spell_name="Starsurge",
            is_overflow=False,
        )
        assert p.timestamp_sec == 10.5
        assert p.value == 80
        assert p.max_value == 100
        assert p.spell_name == "Starsurge"
        assert p.is_overflow is False

    def test_missing_timestamp_sec_raises(self):
        """缺少 timestamp_sec 被拒绝"""
        with pytest.raises(ValidationError):
            ResourcePoint(
                value=80,
                max_value=100,
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        p = ResourcePoint(timestamp_sec=5.0)
        assert p.value == 0
        assert p.max_value == 0
        assert p.spell_name == ""
        assert p.is_overflow is False

    def test_overflow_flag(self):
        """is_overflow 标记正确"""
        p = ResourcePoint(
            timestamp_sec=10.0,
            value=100,
            max_value=100,
            is_overflow=True,
        )
        assert p.is_overflow is True

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = ResourcePoint(
            timestamp_sec=15.0,
            value=90,
            max_value=100,
            spell_name="Wrath",
            is_overflow=False,
        )
        data = original.model_dump()
        rebuilt = ResourcePoint(**data)
        assert rebuilt.timestamp_sec == original.timestamp_sec
        assert rebuilt.value == original.value
        assert rebuilt.max_value == original.max_value
        assert rebuilt.spell_name == original.spell_name
        assert rebuilt.is_overflow == original.is_overflow


# ============================================================
# 模型测试 — ResourceTimelineResponse
# ============================================================
class TestResourceTimelineResponseModel:
    """ResourceTimelineResponse 数据模型验证。"""

    def test_valid_construction(self):
        """有效资源时间线响应通过验证"""
        resp = ResourceTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            resource_type="astral_power",
            fight_duration=300.0,
            total_points=50,
            overflow_count=3,
            overflow_pct=6.0,
            points=[
                ResourcePoint(timestamp_sec=5.0, value=50, max_value=100),
                ResourcePoint(timestamp_sec=10.0, value=100, max_value=100, is_overflow=True),
            ],
        )
        assert resp.report_code == "ABC123"
        assert resp.resource_type == "astral_power"
        assert resp.total_points == 50
        assert resp.overflow_count == 3
        assert resp.overflow_pct == 6.0
        assert len(resp.points) == 2

    def test_missing_report_code_raises(self):
        """缺少 report_code 被拒绝"""
        with pytest.raises(ValidationError):
            ResourceTimelineResponse(
                fight_id=3,
                player_name="Moonkin",
                resource_type="astral_power",
            )  # type: ignore

    def test_missing_resource_type_raises(self):
        """缺少 resource_type 被拒绝"""
        with pytest.raises(ValidationError):
            ResourceTimelineResponse(
                report_code="ABC123",
                fight_id=3,
                player_name="Moonkin",
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        resp = ResourceTimelineResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="TestPlayer",
            resource_type="mana",
        )
        assert resp.fight_duration == 0.0
        assert resp.total_points == 0
        assert resp.overflow_count == 0
        assert resp.overflow_pct == 0.0
        assert resp.points == []

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = ResourceTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            resource_type="astral_power",
            fight_duration=300.0,
            total_points=100,
            overflow_count=5,
            overflow_pct=5.0,
            points=[
                ResourcePoint(timestamp_sec=5.0, value=50, max_value=100),
                ResourcePoint(timestamp_sec=10.0, value=100, max_value=100, is_overflow=True),
            ],
        )
        data = original.model_dump()
        rebuilt = ResourceTimelineResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert rebuilt.resource_type == original.resource_type
        assert rebuilt.fight_duration == original.fight_duration
        assert rebuilt.total_points == original.total_points
        assert rebuilt.overflow_count == original.overflow_count
        assert rebuilt.overflow_pct == original.overflow_pct
        assert len(rebuilt.points) == 2
        assert rebuilt.points[1].is_overflow is True


# ============================================================
# 单元测试 — 溢出检测
# ============================================================
class TestDetectOverflow:
    """资源溢出检测逻辑。"""

    def test_at_cap(self):
        """资源值 == 上限 -> 溢出"""
        assert _detect_overflow(100, 100) is True

    def test_over_cap(self):
        """资源值 > 上限（理论不该发生，但防御）-> 溢出"""
        assert _detect_overflow(110, 100) is True

    def test_below_cap(self):
        """资源值 < 上限 -> 不溢出"""
        assert _detect_overflow(80, 100) is False

    def test_zero_value(self):
        """资源值为 0 -> 不溢出"""
        assert _detect_overflow(0, 100) is False

    def test_zero_max(self):
        """max_value 为 0 -> 不溢出（无上限）"""
        assert _detect_overflow(100, 0) is False

    def test_negative_max(self):
        """max_value 为负 -> 不溢出（无效上限）"""
        assert _detect_overflow(100, -1) is False

    def test_both_zero(self):
        """都为 0 -> 不溢出"""
        assert _detect_overflow(0, 0) is False


# ============================================================
# 单元测试 — 封顶时间计算
# ============================================================
class TestCalcTimeAtCap:
    """资源封顶时间百分比计算逻辑。"""

    def test_never_capped(self):
        """从未达到上限 -> 0%"""
        points = [
            {"timestamp_sec": 0.0, "value": 50, "max_value": 100},
            {"timestamp_sec": 50.0, "value": 60, "max_value": 100},
            {"timestamp_sec": 100.0, "value": 70, "max_value": 100},
        ]
        result = _calc_time_at_cap(points, 100.0)
        assert result == 0.0

    def test_always_capped(self):
        """全程封顶 -> 100%"""
        points = [
            {"timestamp_sec": 0.0, "value": 100, "max_value": 100},
        ]
        result = _calc_time_at_cap(points, 100.0)
        assert abs(result - 100.0) < 0.01

    def test_partially_capped(self):
        """部分时间封顶"""
        # 0-50s: at cap, 50-100s: not at cap
        points = [
            {"timestamp_sec": 0.0, "value": 100, "max_value": 100},
            {"timestamp_sec": 50.0, "value": 50, "max_value": 100},
        ]
        result = _calc_time_at_cap(points, 100.0)
        assert abs(result - 50.0) < 0.01

    def test_empty_points(self):
        """空数据 -> 0%"""
        result = _calc_time_at_cap([], 100.0)
        assert result == 0.0

    def test_zero_fight_duration(self):
        """战斗时长为 0 -> 0%"""
        points = [
            {"timestamp_sec": 0.0, "value": 100, "max_value": 100},
        ]
        result = _calc_time_at_cap(points, 0.0)
        assert result == 0.0

    def test_multiple_cap_windows(self):
        """多次封顶窗口"""
        # 0-10: capped, 10-30: not, 30-50: capped, 50-100: not
        points = [
            {"timestamp_sec": 0.0, "value": 100, "max_value": 100},
            {"timestamp_sec": 10.0, "value": 50, "max_value": 100},
            {"timestamp_sec": 30.0, "value": 100, "max_value": 100},
            {"timestamp_sec": 50.0, "value": 30, "max_value": 100},
        ]
        result = _calc_time_at_cap(points, 100.0)
        # capped: 10s + 20s = 30s / 100s = 30%
        assert abs(result - 30.0) < 0.01

    def test_no_max_value(self):
        """max_value 为 0 -> 不算封顶"""
        points = [
            {"timestamp_sec": 0.0, "value": 100, "max_value": 0},
        ]
        result = _calc_time_at_cap(points, 100.0)
        assert result == 0.0


# ============================================================
# 集成测试 — ResourceTimelineResponse 完整构造
# ============================================================
class TestResourceTimelineIntegration:
    """ResourceTimelineResponse 完整构造与序列化集成测试。"""

    def test_populated_response_round_trip(self):
        """包含多个数据点的完整响应序列化往返"""
        points = [
            ResourcePoint(timestamp_sec=0.0, value=0, max_value=100),
            ResourcePoint(timestamp_sec=5.0, value=30, max_value=100, spell_name="Wrath"),
            ResourcePoint(timestamp_sec=10.0, value=60, max_value=100, spell_name="Wrath"),
            ResourcePoint(timestamp_sec=15.0, value=100, max_value=100, spell_name="Wrath", is_overflow=True),
            ResourcePoint(timestamp_sec=16.0, value=70, max_value=100, spell_name="Starsurge"),
        ]
        resp = ResourceTimelineResponse(
            report_code="XYZ789",
            fight_id=5,
            player_name="Moonkin",
            resource_type="astral_power",
            fight_duration=180.0,
            total_points=5,
            overflow_count=1,
            overflow_pct=20.0,
            points=points,
        )
        data = resp.model_dump()
        rebuilt = ResourceTimelineResponse(**data)
        assert rebuilt.total_points == 5
        assert len(rebuilt.points) == 5
        assert rebuilt.points[3].is_overflow is True
        assert rebuilt.points[4].spell_name == "Starsurge"

    def test_minimal_response(self):
        """最小响应（无数据点）"""
        resp = ResourceTimelineResponse(
            report_code="MIN",
            fight_id=1,
            player_name="Player",
            resource_type="mana",
        )
        data = resp.model_dump()
        assert data["points"] == []
        assert data["total_points"] == 0
        assert data["overflow_count"] == 0
