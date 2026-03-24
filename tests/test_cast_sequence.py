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


# ============================================================
# 集成测试 — get_cast_sequence 完整工具管线（MockWCLClient）
# ============================================================

from tests.conftest import MockWCLClient
from src.tools.cast_sequence import get_cast_sequence


# ----------------------------------------------------------
# 共享 mock 数据
# ----------------------------------------------------------

_FIGHT_RESPONSE = {
    "reportData": {
        "report": {
            "fights": [{
                "startTime": 100000,
                "endTime": 130000,
                "encounterID": 3001,
                "name": "Test Boss",
                "kill": True,
            }]
        }
    }
}

_MASTER_DATA_RESPONSE = {
    "reportData": {
        "report": {
            "masterData": {
                "actors": [
                    {"id": 5, "name": "TestPlayer", "type": "Player"},
                    {"id": 6, "name": "OtherPlayer", "type": "Player"},
                ],
                "abilities": [
                    {"gameID": 190984, "name": "Wrath"},
                    {"gameID": 194153, "name": "Starfire"},
                    {"gameID": 78674, "name": "Starsurge"},
                ],
            }
        }
    }
}

_CAST_EVENTS_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 190984, "timestamp": 102000},
                    {"type": "cast", "abilityGameID": 194153, "timestamp": 105000},
                    {"type": "cast", "abilityGameID": 78674, "timestamp": 108000},
                    {"type": "cast", "abilityGameID": 190984, "timestamp": 112000},
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}


def _make_client(
    fight=_FIGHT_RESPONSE,
    master=_MASTER_DATA_RESPONSE,
    casts=_CAST_EVENTS_RESPONSE,
) -> MockWCLClient:
    """构造配置好三个查询响应的 MockWCLClient"""
    client = MockWCLClient()
    client.set_response("fights", fight)
    client.set_response("masterData", master)
    client.set_response("dataType: Casts", casts)
    return client


class TestGetCastSequenceIntegration:
    """get_cast_sequence 完整管线集成测试。"""

    # ----------------------------------------------------------
    # 1. 基本管线验证
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_basic_cast_sequence(self):
        """完整管线: 3个查询 -> CastSequenceResponse 字段正确"""
        client = _make_client()
        resp = await get_cast_sequence(
            client, "ABC123", fight_id=1,
            player="TestPlayer", spec="balance-druid",
        )

        assert resp.report_code == "ABC123"
        assert resp.fight_id == 1
        assert resp.player_name == "TestPlayer"
        assert resp.spec == "balance-druid"
        assert resp.total_casts == 4
        # fight_duration = (130000 - 100000) / 1000 = 30.0
        assert resp.fight_duration == 30.0
        assert len(resp.casts) == 4
        # 第一个施法: (102000 - 100000) / 1000 = 2.0 秒
        assert resp.casts[0].spell_name == "Wrath"
        assert resp.casts[0].timestamp_sec == 2.0

    # ----------------------------------------------------------
    # 2. URL 解析
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_url_parsing(self):
        """传入完整 WCL URL，report_code 应被正确提取"""
        client = _make_client()
        resp = await get_cast_sequence(
            client,
            "https://www.warcraftlogs.com/reports/XYZ789abc#fight=1",
            fight_id=1,
            player="TestPlayer",
            spec="balance-druid",
        )
        assert resp.report_code == "XYZ789abc"

    # ----------------------------------------------------------
    # 3. 时间范围过滤
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_time_range_filtering(self):
        """设置 time_start/time_end，仅返回范围内事件"""
        # 事件时间戳（相对）: 2s, 5s, 8s, 12s
        # time_start=3, time_end=9 -> query_start=103000, query_end=109000
        # 模拟 WCL 只返回该范围内的事件
        filtered_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "cast", "abilityGameID": 194153, "timestamp": 105000},
                            {"type": "cast", "abilityGameID": 78674, "timestamp": 108000},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client(casts=filtered_events)
        resp = await get_cast_sequence(
            client, "ABC123", fight_id=1,
            player="TestPlayer", spec="balance-druid",
            time_start=3.0, time_end=9.0,
        )

        assert resp.total_casts == 2
        assert resp.time_start == 3.0
        assert resp.time_end == 9.0
        assert resp.casts[0].spell_name == "Starfire"
        assert resp.casts[1].spell_name == "Starsurge"

    # ----------------------------------------------------------
    # 4. 资源提取
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_resource_extraction(self):
        """classResources 中的资源值应映射到 CastEvent"""
        events_with_resources = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {
                                "type": "cast",
                                "abilityGameID": 78674,
                                "timestamp": 103000,
                                "classResources": [
                                    {"amount": 40, "max": 100, "type": 8}
                                ],
                            },
                            {
                                "type": "cast",
                                "abilityGameID": 190984,
                                "timestamp": 106000,
                            },
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client(casts=events_with_resources)
        resp = await get_cast_sequence(
            client, "ABC123", fight_id=1,
            player="TestPlayer", spec="balance-druid",
        )

        # 第一个事件有资源信息
        assert resp.casts[0].resource_amount == 40.0
        assert resp.casts[0].resource_max == 100.0
        # 第二个事件无资源信息
        assert resp.casts[1].resource_amount is None
        assert resp.casts[1].resource_max is None

    # ----------------------------------------------------------
    # 5. 玩家未找到 -> ValueError
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_player_not_found(self):
        """玩家名不匹配任何 actor -> 抛出 ValueError"""
        client = _make_client()
        with pytest.raises(ValueError, match="未找到玩家"):
            await get_cast_sequence(
                client, "ABC123", fight_id=1,
                player="NonExistentPlayer", spec="balance-druid",
            )

    # ----------------------------------------------------------
    # 6. 战斗未找到 -> ValueError
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_fight_not_found(self):
        """空 fights 响应 -> 抛出 ValueError"""
        empty_fights = {
            "reportData": {
                "report": {
                    "fights": []
                }
            }
        }
        client = _make_client(fight=empty_fights)
        with pytest.raises(ValueError, match="未找到战斗"):
            await get_cast_sequence(
                client, "ABC123", fight_id=99,
                player="TestPlayer", spec="balance-druid",
            )

    # ----------------------------------------------------------
    # 7. 事件按时间排序
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_casts_sorted_by_time(self):
        """乱序到达的事件，输出应按时间排序"""
        unordered_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "cast", "abilityGameID": 78674, "timestamp": 115000},
                            {"type": "cast", "abilityGameID": 190984, "timestamp": 102000},
                            {"type": "cast", "abilityGameID": 194153, "timestamp": 108000},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client(casts=unordered_events)
        resp = await get_cast_sequence(
            client, "ABC123", fight_id=1,
            player="TestPlayer", spec="balance-druid",
        )

        timestamps = [c.timestamp_sec for c in resp.casts]
        assert timestamps == sorted(timestamps)
        assert resp.casts[0].spell_name == "Wrath"       # 102000 -> 2.0s
        assert resp.casts[1].spell_name == "Starfire"     # 108000 -> 8.0s
        assert resp.casts[2].spell_name == "Starsurge"    # 115000 -> 15.0s

    # ----------------------------------------------------------
    # 8. 过滤非 cast 类型事件
    # ----------------------------------------------------------
    @pytest.mark.asyncio
    async def test_filters_non_cast_events(self):
        """begincast 等非 cast 类型事件应被排除"""
        mixed_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "begincast", "abilityGameID": 190984, "timestamp": 101000},
                            {"type": "cast", "abilityGameID": 190984, "timestamp": 102500},
                            {"type": "begincast", "abilityGameID": 194153, "timestamp": 104000},
                            {"type": "cast", "abilityGameID": 194153, "timestamp": 105500},
                            {"type": "begincast", "abilityGameID": 78674, "timestamp": 107000},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client(casts=mixed_events)
        resp = await get_cast_sequence(
            client, "ABC123", fight_id=1,
            player="TestPlayer", spec="balance-druid",
        )

        # 只有 2 个 cast 事件，3 个 begincast 被过滤
        assert resp.total_casts == 2
        assert resp.casts[0].spell_name == "Wrath"
        assert resp.casts[1].spell_name == "Starfire"
