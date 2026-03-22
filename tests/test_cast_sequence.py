# ============================================================
# Phase 7: 施法序列 (CastSequence) 测试
# 覆盖模型验证、时间范围过滤、集成测试
#
# 测试策略:
#   - 模型测试: CastEvent, CastSequenceResponse 验证
#   - 纯单元测试: 时间范围过滤逻辑（不依赖实现）
#   - 集成测试: CastSequenceResponse 完整构造与序列化
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    CastEvent,
    CastSequenceResponse,
)


# ============================================================
# 辅助函数 — 时间范围过滤（纯逻辑，镜像 cast_sequence 工具预期行为）
# ============================================================
def _filter_by_time_range(
    events: list[dict],
    time_start: float,
    time_end: float,
) -> list[dict]:
    """
    按时间范围过滤施法事件。

    如果 time_start == 0 且 time_end == 0，返回所有事件（全量）。
    否则按 [time_start, time_end] 闭区间过滤。
    """
    if time_start == 0.0 and time_end == 0.0:
        return list(events)
    return [
        e for e in events
        if time_start <= e["timestamp_sec"] <= time_end
    ]


# ============================================================
# 模型测试 — CastEvent
# ============================================================
class TestCastEventModel:
    """CastEvent 数据模型验证。"""

    def test_valid_construction(self):
        """有效施法事件数据通过验证"""
        e = CastEvent(
            spell_id=49020,
            spell_name="Obliterate",
            timestamp_sec=15.3,
        )
        assert e.spell_id == 49020
        assert e.spell_name == "Obliterate"
        assert e.timestamp_sec == 15.3

    def test_missing_spell_id_raises(self):
        """缺少 spell_id 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(
                spell_name="Obliterate",
                timestamp_sec=15.3,
            )  # type: ignore

    def test_missing_spell_name_raises(self):
        """缺少 spell_name 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(
                spell_id=49020,
                timestamp_sec=15.3,
            )  # type: ignore

    def test_missing_timestamp_sec_raises(self):
        """缺少 timestamp_sec 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(
                spell_id=49020,
                spell_name="Obliterate",
            )  # type: ignore

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CastEvent(
            spell_id=49020,
            spell_name="Obliterate",
            timestamp_sec=15.3,
        )
        data = original.model_dump()
        rebuilt = CastEvent(**data)
        assert rebuilt.spell_id == original.spell_id
        assert rebuilt.spell_name == original.spell_name
        assert rebuilt.timestamp_sec == original.timestamp_sec


# ============================================================
# 模型测试 — CastSequenceResponse
# ============================================================
class TestCastSequenceResponseModel:
    """CastSequenceResponse 数据模型验证。"""

    def test_valid_construction(self):
        """有效施法序列响应通过验证"""
        resp = CastSequenceResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            fight_duration=300.0,
            time_start=10.0,
            time_end=50.0,
            total_casts=5,
            casts=[
                CastEvent(spell_id=49020, spell_name="Obliterate", timestamp_sec=10.5),
                CastEvent(spell_id=49143, spell_name="Frost Strike", timestamp_sec=12.0),
            ],
        )
        assert resp.report_code == "ABC123"
        assert resp.fight_id == 3
        assert resp.player_name == "Frostblade"
        assert resp.spec == "frost-death-knight"
        assert resp.fight_duration == 300.0
        assert resp.time_start == 10.0
        assert resp.time_end == 50.0
        assert resp.total_casts == 5
        assert len(resp.casts) == 2

    def test_missing_report_code_raises(self):
        """缺少 report_code 被拒绝"""
        with pytest.raises(ValidationError):
            CastSequenceResponse(
                fight_id=3,
                player_name="Frostblade",
                spec="frost-death-knight",
            )  # type: ignore

    def test_missing_fight_id_raises(self):
        """缺少 fight_id 被拒绝"""
        with pytest.raises(ValidationError):
            CastSequenceResponse(
                report_code="ABC123",
                player_name="Frostblade",
                spec="frost-death-knight",
            )  # type: ignore

    def test_missing_player_name_raises(self):
        """缺少 player_name 被拒绝"""
        with pytest.raises(ValidationError):
            CastSequenceResponse(
                report_code="ABC123",
                fight_id=3,
                spec="frost-death-knight",
            )  # type: ignore

    def test_missing_spec_raises(self):
        """缺少 spec 被拒绝"""
        with pytest.raises(ValidationError):
            CastSequenceResponse(
                report_code="ABC123",
                fight_id=3,
                player_name="Frostblade",
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        resp = CastSequenceResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="TestPlayer",
            spec="balance-druid",
        )
        assert resp.fight_duration == 0.0
        assert resp.time_start == 0.0
        assert resp.time_end == 0.0
        assert resp.total_casts == 0
        assert resp.casts == []

    def test_empty_casts(self):
        """空 casts 列表有效"""
        resp = CastSequenceResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="TestPlayer",
            spec="balance-druid",
            casts=[],
        )
        assert resp.casts == []

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CastSequenceResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            fight_duration=300.0,
            time_start=0.0,
            time_end=300.0,
            total_casts=3,
            casts=[
                CastEvent(spell_id=49020, spell_name="Obliterate", timestamp_sec=10.5),
                CastEvent(spell_id=49143, spell_name="Frost Strike", timestamp_sec=12.0),
                CastEvent(spell_id=51271, spell_name="Pillar of Frost", timestamp_sec=15.0),
            ],
        )
        data = original.model_dump()
        rebuilt = CastSequenceResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert rebuilt.fight_id == original.fight_id
        assert rebuilt.player_name == original.player_name
        assert rebuilt.spec == original.spec
        assert rebuilt.fight_duration == original.fight_duration
        assert rebuilt.total_casts == original.total_casts
        assert len(rebuilt.casts) == 3
        assert rebuilt.casts[0].spell_name == "Obliterate"
        assert rebuilt.casts[2].spell_name == "Pillar of Frost"


# ============================================================
# 单元测试 — 时间范围过滤
# ============================================================
class TestTimeRangeFiltering:
    """施法事件时间范围过滤逻辑。"""

    def _make_events(self) -> list[dict]:
        """构造测试事件列表"""
        return [
            {"spell_id": 1, "spell_name": "A", "timestamp_sec": 5.0},
            {"spell_id": 2, "spell_name": "B", "timestamp_sec": 15.0},
            {"spell_id": 3, "spell_name": "C", "timestamp_sec": 25.0},
            {"spell_id": 4, "spell_name": "D", "timestamp_sec": 35.0},
            {"spell_id": 5, "spell_name": "E", "timestamp_sec": 45.0},
        ]

    def test_full_fight_no_range(self):
        """time_start=0, time_end=0 -> 返回所有事件"""
        events = self._make_events()
        result = _filter_by_time_range(events, 0.0, 0.0)
        assert len(result) == 5

    def test_specified_range_filters(self):
        """指定范围 [10, 30] -> 只返回范围内事件"""
        events = self._make_events()
        result = _filter_by_time_range(events, 10.0, 30.0)
        assert len(result) == 2
        assert result[0]["timestamp_sec"] == 15.0
        assert result[1]["timestamp_sec"] == 25.0

    def test_range_inclusive_boundaries(self):
        """范围包含边界值"""
        events = self._make_events()
        result = _filter_by_time_range(events, 15.0, 35.0)
        assert len(result) == 3
        assert result[0]["timestamp_sec"] == 15.0
        assert result[2]["timestamp_sec"] == 35.0

    def test_time_start_equals_time_end(self):
        """time_start == time_end -> 只返回恰好在该时刻的事件"""
        events = self._make_events()
        result = _filter_by_time_range(events, 25.0, 25.0)
        assert len(result) == 1
        assert result[0]["timestamp_sec"] == 25.0

    def test_time_start_equals_time_end_no_match(self):
        """time_start == time_end 但无匹配 -> 空列表"""
        events = self._make_events()
        result = _filter_by_time_range(events, 20.0, 20.0)
        assert len(result) == 0

    def test_time_start_greater_than_time_end(self):
        """time_start > time_end -> 空列表（无效范围）"""
        events = self._make_events()
        result = _filter_by_time_range(events, 30.0, 10.0)
        assert len(result) == 0

    def test_empty_events(self):
        """空事件列表 -> 空结果"""
        result = _filter_by_time_range([], 0.0, 0.0)
        assert result == []

    def test_range_before_all_events(self):
        """范围在所有事件之前 -> 空结果"""
        events = self._make_events()
        result = _filter_by_time_range(events, 0.0, 3.0)
        assert len(result) == 0

    def test_range_after_all_events(self):
        """范围在所有事件之后 -> 空结果"""
        events = self._make_events()
        result = _filter_by_time_range(events, 50.0, 100.0)
        assert len(result) == 0


# ============================================================
# 集成测试 — CastSequenceResponse 完整构造
# ============================================================
class TestCastSequenceIntegration:
    """CastSequenceResponse 完整构造与序列化集成测试。"""

    def test_populated_response_round_trip(self):
        """包含多个事件的完整响应序列化往返"""
        events = [
            CastEvent(spell_id=49020, spell_name="Obliterate", timestamp_sec=1.5),
            CastEvent(spell_id=49143, spell_name="Frost Strike", timestamp_sec=3.0),
            CastEvent(spell_id=51271, spell_name="Pillar of Frost", timestamp_sec=4.5),
            CastEvent(spell_id=49020, spell_name="Obliterate", timestamp_sec=6.0),
            CastEvent(spell_id=49020, spell_name="Obliterate", timestamp_sec=7.5),
        ]
        resp = CastSequenceResponse(
            report_code="XYZ789",
            fight_id=5,
            player_name="Moonkin",
            spec="balance-druid",
            fight_duration=180.0,
            time_start=0.0,
            time_end=10.0,
            total_casts=5,
            casts=events,
        )
        data = resp.model_dump()
        rebuilt = CastSequenceResponse(**data)
        assert rebuilt.total_casts == 5
        assert len(rebuilt.casts) == 5
        assert rebuilt.casts[0].spell_id == 49020
        assert rebuilt.casts[4].timestamp_sec == 7.5

    def test_minimal_response(self):
        """最小响应（无事件）"""
        resp = CastSequenceResponse(
            report_code="MIN",
            fight_id=1,
            player_name="Player",
            spec="arms-warrior",
        )
        data = resp.model_dump()
        assert data["casts"] == []
        assert data["total_casts"] == 0
        assert data["fight_duration"] == 0.0
