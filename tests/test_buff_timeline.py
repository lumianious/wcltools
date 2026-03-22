# ============================================================
# Phase 7: Buff 时间线 (BuffTimeline) 测试
# 覆盖模型验证、覆盖率计算、平均层数计算、集成测试
#
# 测试策略:
#   - 模型测试: BuffEvent, BuffSummary, BuffTimelineResponse 验证
#   - 纯单元测试: 覆盖率计算、平均层数计算（不依赖实现）
#   - 集成测试: BuffTimelineResponse 完整构造与序列化
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    BuffEvent,
    BuffSummary,
    BuffTimelineResponse,
)


# ============================================================
# 辅助函数 — 覆盖率计算（纯逻辑，镜像 buff_timeline 工具预期行为）
# ============================================================
def _calc_uptime_pct(
    apply_remove_pairs: list[tuple[float, float]],
    fight_duration: float,
) -> float:
    """
    计算 Buff 覆盖率。

    apply_remove_pairs: [(apply_sec, remove_sec), ...]
    返回覆盖率百分比 [0, 100]。
    """
    if fight_duration <= 0:
        return 0.0
    total_uptime = sum(
        remove_sec - apply_sec
        for apply_sec, remove_sec in apply_remove_pairs
    )
    return min(total_uptime / fight_duration * 100.0, 100.0)


def _calc_avg_stacks(
    stack_events: list[tuple[float, int]],
    fight_duration: float,
) -> float:
    """
    计算 Buff 平均层数。

    stack_events: [(timestamp_sec, stacks), ...] 按时间排序
    对每个时间段加权计算平均层数。
    """
    if fight_duration <= 0 or not stack_events:
        return 0.0

    total_weighted = 0.0
    for i, (ts, stacks) in enumerate(stack_events):
        if i + 1 < len(stack_events):
            next_ts = stack_events[i + 1][0]
        else:
            next_ts = fight_duration
        duration = next_ts - ts
        total_weighted += stacks * duration

    return total_weighted / fight_duration


# ============================================================
# 模型测试 — BuffEvent
# ============================================================
class TestBuffEventModel:
    """BuffEvent 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Buff 事件数据通过验证"""
        e = BuffEvent(
            buff_id=51271,
            buff_name="Pillar of Frost",
            event_type="applybuff",
            timestamp_sec=10.0,
            stacks=1,
        )
        assert e.buff_id == 51271
        assert e.buff_name == "Pillar of Frost"
        assert e.event_type == "applybuff"
        assert e.timestamp_sec == 10.0
        assert e.stacks == 1

    def test_missing_buff_id_raises(self):
        """缺少 buff_id 被拒绝"""
        with pytest.raises(ValidationError):
            BuffEvent(
                buff_name="Pillar of Frost",
                event_type="applybuff",
                timestamp_sec=10.0,
            )  # type: ignore

    def test_missing_buff_name_raises(self):
        """缺少 buff_name 被拒绝"""
        with pytest.raises(ValidationError):
            BuffEvent(
                buff_id=51271,
                event_type="applybuff",
                timestamp_sec=10.0,
            )  # type: ignore

    def test_missing_event_type_raises(self):
        """缺少 event_type 被拒绝"""
        with pytest.raises(ValidationError):
            BuffEvent(
                buff_id=51271,
                buff_name="Pillar of Frost",
                timestamp_sec=10.0,
            )  # type: ignore

    def test_missing_timestamp_sec_raises(self):
        """缺少 timestamp_sec 被拒绝"""
        with pytest.raises(ValidationError):
            BuffEvent(
                buff_id=51271,
                buff_name="Pillar of Frost",
                event_type="applybuff",
            )  # type: ignore

    def test_stacks_defaults_to_zero(self):
        """stacks 默认为 0"""
        e = BuffEvent(
            buff_id=51271,
            buff_name="Pillar of Frost",
            event_type="applybuff",
            timestamp_sec=10.0,
        )
        assert e.stacks == 0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = BuffEvent(
            buff_id=51271,
            buff_name="Pillar of Frost",
            event_type="removebuff",
            timestamp_sec=22.0,
            stacks=0,
        )
        data = original.model_dump()
        rebuilt = BuffEvent(**data)
        assert rebuilt.buff_id == original.buff_id
        assert rebuilt.buff_name == original.buff_name
        assert rebuilt.event_type == original.event_type
        assert rebuilt.timestamp_sec == original.timestamp_sec
        assert rebuilt.stacks == original.stacks


# ============================================================
# 模型测试 — BuffSummary
# ============================================================
class TestBuffSummaryModel:
    """BuffSummary 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Buff 摘要数据通过验证"""
        s = BuffSummary(
            buff_id=51271,
            buff_name="Pillar of Frost",
            uptime_pct=40.0,
            avg_stacks=1.0,
            apply_count=3,
            events=[
                BuffEvent(
                    buff_id=51271,
                    buff_name="Pillar of Frost",
                    event_type="applybuff",
                    timestamp_sec=10.0,
                ),
            ],
        )
        assert s.buff_id == 51271
        assert s.uptime_pct == 40.0
        assert s.avg_stacks == 1.0
        assert s.apply_count == 3
        assert len(s.events) == 1

    def test_missing_buff_id_raises(self):
        """缺少 buff_id 被拒绝"""
        with pytest.raises(ValidationError):
            BuffSummary(
                buff_name="Pillar of Frost",
            )  # type: ignore

    def test_missing_buff_name_raises(self):
        """缺少 buff_name 被拒绝"""
        with pytest.raises(ValidationError):
            BuffSummary(
                buff_id=51271,
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        s = BuffSummary(
            buff_id=51271,
            buff_name="Pillar of Frost",
        )
        assert s.uptime_pct == 0.0
        assert s.avg_stacks == 0.0
        assert s.apply_count == 0
        assert s.events == []

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = BuffSummary(
            buff_id=51271,
            buff_name="Pillar of Frost",
            uptime_pct=40.0,
            avg_stacks=1.0,
            apply_count=3,
            events=[
                BuffEvent(
                    buff_id=51271,
                    buff_name="Pillar of Frost",
                    event_type="applybuff",
                    timestamp_sec=10.0,
                ),
                BuffEvent(
                    buff_id=51271,
                    buff_name="Pillar of Frost",
                    event_type="removebuff",
                    timestamp_sec=22.0,
                ),
            ],
        )
        data = original.model_dump()
        rebuilt = BuffSummary(**data)
        assert rebuilt.buff_id == original.buff_id
        assert rebuilt.uptime_pct == original.uptime_pct
        assert rebuilt.avg_stacks == original.avg_stacks
        assert rebuilt.apply_count == original.apply_count
        assert len(rebuilt.events) == 2


# ============================================================
# 模型测试 — BuffTimelineResponse
# ============================================================
class TestBuffTimelineResponseModel:
    """BuffTimelineResponse 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Buff 时间线响应通过验证"""
        resp = BuffTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            fight_duration=300.0,
            time_start=0.0,
            time_end=300.0,
            buffs=[
                BuffSummary(
                    buff_id=51271,
                    buff_name="Pillar of Frost",
                    uptime_pct=40.0,
                ),
            ],
        )
        assert resp.report_code == "ABC123"
        assert resp.fight_duration == 300.0
        assert len(resp.buffs) == 1

    def test_missing_report_code_raises(self):
        """缺少 report_code 被拒绝"""
        with pytest.raises(ValidationError):
            BuffTimelineResponse(
                fight_id=3,
                player_name="Frostblade",
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        resp = BuffTimelineResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="TestPlayer",
        )
        assert resp.fight_duration == 0.0
        assert resp.time_start == 0.0
        assert resp.time_end == 0.0
        assert resp.buffs == []

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = BuffTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            fight_duration=300.0,
            buffs=[
                BuffSummary(
                    buff_id=51271,
                    buff_name="Pillar of Frost",
                    uptime_pct=40.0,
                    avg_stacks=1.0,
                    apply_count=3,
                ),
            ],
        )
        data = original.model_dump()
        rebuilt = BuffTimelineResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert rebuilt.fight_duration == original.fight_duration
        assert len(rebuilt.buffs) == 1
        assert rebuilt.buffs[0].buff_name == "Pillar of Frost"
        assert rebuilt.buffs[0].uptime_pct == 40.0


# ============================================================
# 单元测试 — Buff 覆盖率计算
# ============================================================
class TestCalcUptimePct:
    """Buff 覆盖率百分比计算逻辑。"""

    def test_100_percent_uptime(self):
        """全程覆盖 -> 100%"""
        result = _calc_uptime_pct([(0.0, 300.0)], 300.0)
        assert abs(result - 100.0) < 0.01

    def test_50_percent_uptime(self):
        """半程覆盖 -> 50%"""
        result = _calc_uptime_pct([(0.0, 150.0)], 300.0)
        assert abs(result - 50.0) < 0.01

    def test_0_percent_uptime(self):
        """无覆盖 -> 0%"""
        result = _calc_uptime_pct([], 300.0)
        assert result == 0.0

    def test_multiple_windows(self):
        """多个窗口 -> 正确累加"""
        # 10s + 10s = 20s / 100s = 20%
        result = _calc_uptime_pct([(10.0, 20.0), (50.0, 60.0)], 100.0)
        assert abs(result - 20.0) < 0.01

    def test_overlapping_windows(self):
        """重叠窗口 -> 可能超过 100%，但上限 100%"""
        # (0,200) + (100,300) = 100+200 = 300s / 300s > 100%，但 cap at 100%
        result = _calc_uptime_pct([(0.0, 200.0), (100.0, 300.0)], 300.0)
        assert result == 100.0

    def test_zero_fight_duration(self):
        """战斗时长为 0 -> 0%"""
        result = _calc_uptime_pct([(0.0, 10.0)], 0.0)
        assert result == 0.0

    def test_negative_fight_duration(self):
        """负数战斗时长 -> 0%"""
        result = _calc_uptime_pct([(0.0, 10.0)], -5.0)
        assert result == 0.0

    def test_short_window(self):
        """短窗口 -> 正确百分比"""
        result = _calc_uptime_pct([(50.0, 55.0)], 100.0)
        assert abs(result - 5.0) < 0.01


# ============================================================
# 单元测试 — 平均层数计算
# ============================================================
class TestCalcAvgStacks:
    """Buff 平均层数计算逻辑。"""

    def test_constant_stacks(self):
        """全程 3 层 -> 平均 3.0"""
        events = [(0.0, 3)]
        result = _calc_avg_stacks(events, 100.0)
        assert abs(result - 3.0) < 0.01

    def test_varying_stacks(self):
        """前半 2 层，后半 4 层 -> 平均 3.0"""
        events = [(0.0, 2), (50.0, 4)]
        result = _calc_avg_stacks(events, 100.0)
        # 2*50 + 4*50 = 300 / 100 = 3.0
        assert abs(result - 3.0) < 0.01

    def test_zero_stacks(self):
        """全程 0 层 -> 平均 0.0"""
        events = [(0.0, 0)]
        result = _calc_avg_stacks(events, 100.0)
        assert result == 0.0

    def test_empty_events(self):
        """空事件 -> 0.0"""
        result = _calc_avg_stacks([], 100.0)
        assert result == 0.0

    def test_zero_fight_duration(self):
        """战斗时长为 0 -> 0.0"""
        result = _calc_avg_stacks([(0.0, 5)], 0.0)
        assert result == 0.0

    def test_single_stack_change(self):
        """单次层数变化"""
        # 0-30s: 1 层, 30-100s: 2 层
        events = [(0.0, 1), (30.0, 2)]
        result = _calc_avg_stacks(events, 100.0)
        # 1*30 + 2*70 = 170 / 100 = 1.7
        assert abs(result - 1.7) < 0.01

    def test_multiple_changes(self):
        """多次层数变化"""
        # 0-10: 1, 10-20: 3, 20-30: 0
        events = [(0.0, 1), (10.0, 3), (20.0, 0)]
        result = _calc_avg_stacks(events, 30.0)
        # 1*10 + 3*10 + 0*10 = 40 / 30 ≈ 1.33
        assert abs(result - 1.333) < 0.01


# ============================================================
# 集成测试 — BuffTimelineResponse 完整构造
# ============================================================
class TestBuffTimelineIntegration:
    """BuffTimelineResponse 完整构造与序列化集成测试。"""

    def test_populated_response_round_trip(self):
        """包含多个 Buff 的完整响应序列化往返"""
        buffs = [
            BuffSummary(
                buff_id=51271,
                buff_name="Pillar of Frost",
                uptime_pct=40.0,
                avg_stacks=1.0,
                apply_count=3,
                events=[
                    BuffEvent(buff_id=51271, buff_name="Pillar of Frost",
                              event_type="applybuff", timestamp_sec=10.0),
                    BuffEvent(buff_id=51271, buff_name="Pillar of Frost",
                              event_type="removebuff", timestamp_sec=22.0),
                ],
            ),
            BuffSummary(
                buff_id=194223,
                buff_name="Celestial Alignment",
                uptime_pct=15.0,
                avg_stacks=1.0,
                apply_count=2,
            ),
        ]
        resp = BuffTimelineResponse(
            report_code="XYZ789",
            fight_id=5,
            player_name="Moonkin",
            fight_duration=300.0,
            buffs=buffs,
        )
        data = resp.model_dump()
        rebuilt = BuffTimelineResponse(**data)
        assert len(rebuilt.buffs) == 2
        assert rebuilt.buffs[0].buff_name == "Pillar of Frost"
        assert rebuilt.buffs[0].uptime_pct == 40.0
        assert len(rebuilt.buffs[0].events) == 2
        assert rebuilt.buffs[1].buff_name == "Celestial Alignment"

    def test_minimal_response(self):
        """最小响应（无 Buff）"""
        resp = BuffTimelineResponse(
            report_code="MIN",
            fight_id=1,
            player_name="Player",
        )
        data = resp.model_dump()
        assert data["buffs"] == []
        assert data["fight_duration"] == 0.0
