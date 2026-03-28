# ============================================================
# test_dungeon_analysis.py — analyze_dungeon_run 工具测试
#
# 覆盖: _query_all_fights, _classify_segments,
#        analyze_dungeon_run（聚合 DPS、段落分解、施法统计）
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from tests.conftest import MockWCLClient


# ============================================================
# 共享 mock 数据
# ============================================================

# 5 个战斗段落: 2 boss + 3 trash (+ fight id 0 全局聚合)
MOCK_FIGHTS_RESPONSE = {
    "reportData": {
        "report": {
            "fights": [
                {"id": 0, "startTime": 0, "endTime": 600000, "kill": True, "encounterID": 0, "name": "All"},
                {"id": 1, "startTime": 0, "endTime": 60000, "kill": True, "encounterID": 0, "name": "Trash 1"},
                {"id": 2, "startTime": 65000, "endTime": 165000, "kill": True, "encounterID": 12345, "name": "Boss A"},
                {"id": 3, "startTime": 170000, "endTime": 230000, "kill": True, "encounterID": 0, "name": "Trash 2"},
                {"id": 4, "startTime": 240000, "endTime": 340000, "kill": True, "encounterID": 67890, "name": "Boss B"},
                {"id": 5, "startTime": 350000, "endTime": 400000, "kill": True, "encounterID": 0, "name": "Trash 3"},
            ],
            "title": "Test Dungeon",
        }
    }
}

MOCK_MASTER_DATA_RESPONSE = {
    "reportData": {
        "report": {
            "masterData": {
                "actors": [
                    {"id": 1, "name": "TestPlayer", "type": "Player"},
                    {"id": 2, "name": "OtherPlayer", "type": "Player"},
                ],
                "abilities": [
                    {"gameID": 100, "name": "Fireball"},
                    {"gameID": 101, "name": "Frostbolt"},
                ],
            }
        }
    }
}

# 伤害表 — 全时段聚合
MOCK_DAMAGE_TABLE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {"name": "Fireball", "total": 500000},
                        {"name": "Frostbolt", "total": 300000},
                        {"name": "Melee", "total": 200000},
                    ]
                }
            }
        }
    }
}

# Buff 覆盖率
MOCK_BUFF_TABLE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "auras": [
                        {"name": "Icy Veins", "id": 12472, "totalUptime": 30000, "totalUses": 2},
                    ]
                }
            }
        }
    }
}

# CombatantInfo
MOCK_COMBATANT_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {
                        "talentTree": [
                            {"nodeID": 1001, "id": 2001},
                        ],
                        "gear": [
                            {"id": 12345, "itemLevel": 630, "quality": 4, "name": "Test Helm"},
                        ],
                        "auras": [],
                    }
                ]
            }
        }
    }
}

# 死亡事件
MOCK_DEATH_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"timestamp": 50000, "type": "death"},
                    {"timestamp": 300000, "type": "death"},
                ]
            }
        }
    }
}

# 施法事件（用于 include_casts=True）
MOCK_CAST_EVENTS_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 100, "timestamp": 1000},
                    {"type": "cast", "abilityGameID": 100, "timestamp": 2000},
                    {"type": "cast", "abilityGameID": 101, "timestamp": 3000},
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}

# 每段伤害查询（用于 <= 10 段时的段落 DPS）
MOCK_SEGMENT_DAMAGE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {"name": "Fireball", "total": 100000},
                    ]
                }
            }
        }
    }
}


def _setup_mock_client() -> MockWCLClient:
    """配置完整的 mock client，覆盖所有查询路径。"""
    client = MockWCLClient()
    # 全部战斗列表（查询包含 "fights {" 但不含 "fightIDs"）
    client.set_response("fights {", MOCK_FIGHTS_RESPONSE)
    # masterData
    client.set_response("masterData", MOCK_MASTER_DATA_RESPONSE)
    # 伤害表（全时段）— 用更具体的 key 避免与段落查询冲突
    client.set_response("DamageDone", MOCK_DAMAGE_TABLE_RESPONSE)
    # Buff 表
    client.set_response("Buffs", MOCK_BUFF_TABLE_RESPONSE)
    # CombatantInfo
    client.set_response("CombatantInfo", MOCK_COMBATANT_RESPONSE)
    # Deaths
    client.set_response("Deaths", MOCK_DEATH_RESPONSE)
    # Cast events（分页查询）
    client.set_response("Casts", MOCK_CAST_EVENTS_RESPONSE)
    return client


# ============================================================
# Test 1: _query_all_fights
# ============================================================


@pytest.mark.asyncio
async def test_query_all_fights_returns_fight_list():
    """_query_all_fights 应返回包含所有战斗的列表。"""
    from src.tools.dungeon_analysis import _query_all_fights

    client = _setup_mock_client()
    fights, title = await _query_all_fights(client, "TESTREPORT")
    assert isinstance(fights, list)
    assert len(fights) == 6  # 包含 id=0 的全局聚合
    assert title == "Test Dungeon"


# ============================================================
# Test 2: _classify_segments
# ============================================================


def test_classify_segments_separates_boss_and_trash():
    """_classify_segments 应区分 boss 和 trash 段落，过滤 fight id 0。"""
    from src.tools.dungeon_analysis import _classify_segments

    fights = MOCK_FIGHTS_RESPONSE["reportData"]["report"]["fights"]
    bosses, trash = _classify_segments(fights)
    assert len(bosses) == 2
    assert len(trash) == 3
    assert all(b["encounterID"] > 0 for b in bosses)
    assert all(t["encounterID"] == 0 for t in trash)


# ============================================================
# Test 3: analyze_dungeon_run 聚合 DPS 计算
# ============================================================


@pytest.mark.asyncio
async def test_analyze_dungeon_run_aggregate_dps():
    """DPS 应使用 active_time（各段时长之和），而非 wall-clock。"""
    from src.tools.dungeon_analysis import analyze_dungeon_run

    client = _setup_mock_client()
    result = await analyze_dungeon_run(
        client, report="TESTREPORT", player="TestPlayer",
        spec="frost-mage", include_casts=False,
    )

    # active_time = sum of segment durations (exclude fight id 0)
    # (60 + 100 + 60 + 100 + 50) * 1000 ms = 370000 ms = 370 sec
    expected_active_sec = 370.0
    assert abs(result.active_time_sec - expected_active_sec) < 0.1

    # total_damage = 500000 + 300000 + 200000 = 1000000
    assert result.total_damage == 1000000.0

    # total_dps = total_damage / active_time_sec
    expected_dps = 1000000.0 / 370.0
    assert abs(result.total_dps - expected_dps) < 1.0


# ============================================================
# Test 4: segments 段落分解
# ============================================================


@pytest.mark.asyncio
async def test_analyze_dungeon_run_segments():
    """segments 应包含每场战斗的分解数据（boss/trash 区分）。"""
    from src.tools.dungeon_analysis import analyze_dungeon_run

    client = _setup_mock_client()
    result = await analyze_dungeon_run(
        client, report="TESTREPORT", player="TestPlayer",
        spec="frost-mage", include_casts=False,
    )

    # 5 segments (fight ids 1-5, excluding id 0)
    assert len(result.segments) == 5
    boss_segs = [s for s in result.segments if s.is_boss]
    trash_segs = [s for s in result.segments if not s.is_boss]
    assert len(boss_segs) == 2
    assert len(trash_segs) == 3


# ============================================================
# Test 5: include_casts=False 不查询施法事件
# ============================================================


@pytest.mark.asyncio
async def test_analyze_dungeon_run_no_casts_by_default():
    """include_casts=False 时不应有施法统计。"""
    from src.tools.dungeon_analysis import analyze_dungeon_run

    client = _setup_mock_client()
    result = await analyze_dungeon_run(
        client, report="TESTREPORT", player="TestPlayer",
        spec="frost-mage", include_casts=False,
    )

    assert result.spell_counts == {}


# ============================================================
# Test 6: include_casts=True 填充施法统计
# ============================================================


@pytest.mark.asyncio
async def test_analyze_dungeon_run_with_casts():
    """include_casts=True 时应填充 spell_counts 和 active_time_pct。"""
    from src.tools.dungeon_analysis import analyze_dungeon_run

    client = _setup_mock_client()
    result = await analyze_dungeon_run(
        client, report="TESTREPORT", player="TestPlayer",
        spec="frost-mage", include_casts=True,
    )

    assert len(result.spell_counts) > 0
    assert result.active_time_pct > 0.0


# ============================================================
# Test 7: 模型序列化
# ============================================================


def test_models_serialize_correctly():
    """DungeonRunAnalysisResponse 和 FightSegmentSummary 序列化正确。"""
    from src.models import DungeonRunAnalysisResponse, FightSegmentSummary

    seg = FightSegmentSummary(
        fight_id=1, name="Boss A", is_boss=True,
        duration_sec=120.0, player_dps=5000.0, deaths=1,
    )
    assert seg.model_dump()["is_boss"] is True

    resp = DungeonRunAnalysisResponse(
        report_code="ABC", player_name="Test", spec="frost-mage",
        segments=[seg],
    )
    data = resp.model_dump()
    assert data["report_code"] == "ABC"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["fight_id"] == 1
