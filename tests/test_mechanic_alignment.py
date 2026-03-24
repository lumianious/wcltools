# ============================================================
# Boss Cast Timeline 测试
# 覆盖模型验证、数据函数、集成
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest

from src.models import (
    BossCastEvent,
    BossCastTimelineResponse,
    PlayerAnalysisResponse,
)
from src.data import get_boss


# ============================================================
# 模型测试 — BossCastEvent
# ============================================================
class TestBossCastEventModel:

    def test_valid_construction(self):
        e = BossCastEvent(
            spell_id=1254199,
            spell_name="Parasite Expulsion",
            timestamp_sec=45.0,
        )
        assert e.spell_id == 1254199
        assert e.timestamp_sec == 45.0

    def test_serialization_round_trip(self):
        original = BossCastEvent(
            spell_id=123, spell_name="Test", timestamp_sec=10.0,
        )
        rebuilt = BossCastEvent(**original.model_dump())
        assert rebuilt.spell_id == original.spell_id


# ============================================================
# 模型测试 — BossCastTimelineResponse
# ============================================================
class TestBossCastTimelineResponseModel:

    def test_valid_construction(self):
        r = BossCastTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            encounter_id=3177,
            encounter_name="Vorasius",
            fight_duration=375.0,
            events=[
                BossCastEvent(
                    spell_id=1254199,
                    spell_name="Parasite Expulsion",
                    timestamp_sec=45.0,
                ),
            ],
            spell_summary={"Parasite Expulsion": 1},
        )
        assert r.encounter_name == "Vorasius"
        assert len(r.events) == 1
        assert r.spell_summary["Parasite Expulsion"] == 1

    def test_defaults(self):
        r = BossCastTimelineResponse(report_code="X", fight_id=1)
        assert r.events == []
        assert r.spell_summary == {}
        assert r.encounter_id == 0

    def test_serialization_round_trip(self):
        original = BossCastTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            encounter_id=3177,
            encounter_name="Vorasius",
            fight_duration=375.0,
            events=[
                BossCastEvent(
                    spell_id=1254199,
                    spell_name="Parasite Expulsion",
                    timestamp_sec=45.0,
                ),
                BossCastEvent(
                    spell_id=1254199,
                    spell_name="Parasite Expulsion",
                    timestamp_sec=105.0,
                ),
            ],
            spell_summary={"Parasite Expulsion": 2},
        )
        data = original.model_dump()
        rebuilt = BossCastTimelineResponse(**data)
        assert rebuilt.encounter_name == "Vorasius"
        assert len(rebuilt.events) == 2
        assert rebuilt.spell_summary["Parasite Expulsion"] == 2


# ============================================================
# 数据函数测试 — get_boss
# ============================================================
class TestGetBoss:

    def test_vorasius_exists(self):
        """Vorasius (3177) 在 bosses.json 中"""
        boss = get_boss(3177)
        assert boss is not None
        assert boss["name"] == "Vorasius"
        spell_names = [s["name"] for s in boss["spells"]]
        assert "Parasite Expulsion" in spell_names

    def test_vorasius_spell_ids(self):
        """Vorasius 的技能有正确的 spell_id"""
        boss = get_boss(3177)
        spells_by_name = {s["name"]: s for s in boss["spells"]}
        assert spells_by_name["Parasite Expulsion"]["spell_id"] == 1254199

    def test_nonexistent_boss(self):
        """不存在的 boss → None"""
        assert get_boss(999999) is None

    def test_multiple_void_spire_bosses(self):
        """Void Spire boss 都能查到"""
        for boss_id in [3133, 3177, 3178]:
            boss = get_boss(boss_id)
            assert boss is not None
            assert len(boss["spells"]) > 0


# ============================================================
# 集成测试 — PlayerAnalysisResponse 不再包含 mechanic_alignment
# ============================================================
class TestPlayerAnalysisResponseNoMechanicAlignment:

    def test_no_mechanic_alignment_field(self):
        """PlayerAnalysisResponse 不再有 mechanic_alignment"""
        r = PlayerAnalysisResponse(
            report_code="X", fight_id=1, player_name="T", spec="balance-druid",
        )
        assert not hasattr(r, "mechanic_alignment")


# ============================================================
# 集成测试 — get_boss_cast_timeline
# ============================================================

import copy

from tests.conftest import MockWCLClient
from src.tools.boss_timeline import get_boss_cast_timeline

# ----------------------------------------------------------
# 共享 mock 数据
# ----------------------------------------------------------
# 使用 bosses.json 中不存在的 encounterID，避免 _resolve_ability_ids
# 按技能拆分查询导致 mock 匹配次数不可控
BOSS_FIGHT_INFO = {
    "reportData": {
        "report": {
            "fights": [{
                "id": 3, "startTime": 100000, "endTime": 130000,
                "encounterID": 99999, "name": "Vorasius",
            }]
        }
    }
}

BOSS_CAST_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 474152, "timestamp": 105000,
                     "ability": {"name": "Cosmic Ascent"}},
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}


def _make_client_with_fight_and_events(
    fight_info: dict | None = None,
    events_info: dict | None = None,
) -> MockWCLClient:
    """构建预配置了 fight info + events 的 mock client。"""
    client = MockWCLClient()
    client.set_response("fightIDs", fight_info or BOSS_FIGHT_INFO)
    client.set_response("hostilityType: Enemies", events_info or BOSS_CAST_EVENTS)
    return client


class TestGetBossCastTimelineIntegration:
    """get_boss_cast_timeline 全流程集成测试。"""

    @pytest.mark.asyncio
    async def test_basic_boss_timeline(self):
        """完整管线：fight info + enemy events → BossCastTimelineResponse"""
        client = _make_client_with_fight_and_events()

        result = await get_boss_cast_timeline(client, "ABC123", fight_id=3)

        assert isinstance(result, BossCastTimelineResponse)
        assert result.report_code == "ABC123"
        assert result.fight_id == 3
        assert result.encounter_id == 99999
        assert result.encounter_name == "Vorasius"
        assert result.fight_duration == 30.0  # (130000-100000)/1000
        assert len(result.events) == 1
        assert result.events[0].spell_name == "Cosmic Ascent"
        assert result.events[0].timestamp_sec == 5.0  # (105000-100000)/1000
        assert result.spell_summary["Cosmic Ascent"] == 1

    @pytest.mark.asyncio
    async def test_url_parsing(self):
        """传入完整 URL，验证 report_code 正确提取"""
        client = _make_client_with_fight_and_events()

        result = await get_boss_cast_timeline(
            client,
            "https://www.warcraftlogs.com/reports/XyZ789abc#fight=3",
            fight_id=3,
        )

        assert result.report_code == "XyZ789abc"

    @pytest.mark.asyncio
    async def test_specific_spell_ids(self):
        """传入 spell_ids 过滤，验证只查询指定技能"""
        client = MockWCLClient()
        client.set_response("fightIDs", BOSS_FIGHT_INFO)
        # 按 abilityID 查询时匹配更具体的字符串
        events_resp = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "cast", "abilityGameID": 474152,
                             "timestamp": 110000,
                             "ability": {"name": "Cosmic Ascent"}},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client.set_response("abilityID: 474152", events_resp)

        result = await get_boss_cast_timeline(
            client, "ABC123", fight_id=3, spell_ids=[474152],
        )

        assert len(result.events) == 1
        assert result.events[0].spell_id == 474152

    @pytest.mark.asyncio
    async def test_fight_not_found(self):
        """空 fights 响应 → 抛出 ValueError"""
        client = MockWCLClient()
        empty_fights = {
            "reportData": {
                "report": {
                    "fights": []
                }
            }
        }
        client.set_response("fightIDs", empty_fights)

        with pytest.raises(ValueError, match="未找到战斗"):
            await get_boss_cast_timeline(client, "ABC123", fight_id=99)

    @pytest.mark.asyncio
    async def test_events_sorted_by_time(self):
        """事件乱序到达，验证输出按时间排序"""
        unordered_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "cast", "abilityGameID": 200,
                             "timestamp": 120000,
                             "ability": {"name": "Late Spell"}},
                            {"type": "cast", "abilityGameID": 100,
                             "timestamp": 102000,
                             "ability": {"name": "Early Spell"}},
                            {"type": "cast", "abilityGameID": 300,
                             "timestamp": 115000,
                             "ability": {"name": "Mid Spell"}},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client_with_fight_and_events(events_info=unordered_events)

        result = await get_boss_cast_timeline(client, "ABC123", fight_id=3)

        timestamps = [e.timestamp_sec for e in result.events]
        assert timestamps == sorted(timestamps)
        assert result.events[0].spell_name == "Early Spell"
        assert result.events[-1].spell_name == "Late Spell"

    @pytest.mark.asyncio
    async def test_spell_summary_counts(self):
        """同一技能多次施法，验证 spell_summary 计数"""
        multi_cast_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "cast", "abilityGameID": 474152,
                             "timestamp": 105000,
                             "ability": {"name": "Cosmic Ascent"}},
                            {"type": "cast", "abilityGameID": 474152,
                             "timestamp": 110000,
                             "ability": {"name": "Cosmic Ascent"}},
                            {"type": "cast", "abilityGameID": 474152,
                             "timestamp": 115000,
                             "ability": {"name": "Cosmic Ascent"}},
                            {"type": "cast", "abilityGameID": 999,
                             "timestamp": 108000,
                             "ability": {"name": "Other Spell"}},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client_with_fight_and_events(events_info=multi_cast_events)

        result = await get_boss_cast_timeline(client, "ABC123", fight_id=3)

        assert result.spell_summary["Cosmic Ascent"] == 3
        assert result.spell_summary["Other Spell"] == 1
        assert len(result.events) == 4

    @pytest.mark.asyncio
    async def test_filters_non_cast_events(self):
        """非 cast 事件（如 begincast）被过滤"""
        mixed_events = {
            "reportData": {
                "report": {
                    "events": {
                        "data": [
                            {"type": "begincast", "abilityGameID": 474152,
                             "timestamp": 104000,
                             "ability": {"name": "Cosmic Ascent"}},
                            {"type": "cast", "abilityGameID": 474152,
                             "timestamp": 105000,
                             "ability": {"name": "Cosmic Ascent"}},
                            {"type": "begincast", "abilityGameID": 999,
                             "timestamp": 106000,
                             "ability": {"name": "Other Spell"}},
                        ],
                        "nextPageTimestamp": None,
                    }
                }
            }
        }
        client = _make_client_with_fight_and_events(events_info=mixed_events)

        result = await get_boss_cast_timeline(client, "ABC123", fight_id=3)

        # 只有 type="cast" 的事件被保留
        assert len(result.events) == 1
        assert result.events[0].spell_name == "Cosmic Ascent"
        assert result.events[0].timestamp_sec == 5.0
