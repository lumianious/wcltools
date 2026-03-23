# ============================================================
# Boss Cast Timeline 测试
# 覆盖模型验证、数据函数、集成
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
