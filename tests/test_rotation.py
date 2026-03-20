# ============================================================
# get_rotation_profile 工具测试
# 覆盖 CPM 计算、Buff 覆盖率、集成流程
#
# 测试目标模块: src.tools.rotation (Phase 4)
# 数据模型: SpellStats, BuffUptime, RotationProfileResponse
#
# 测试策略:
#   - 纯单元测试: 直接测试数据处理函数（不依赖具体实现结构）
#   - 集成测试: 通过 mock WCL client 测试完整流程
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.models import (
    BuffUptime,
    RotationProfileResponse,
    SpellStats,
)
from tests.conftest import MockWCLClient


# ============================================================
# 辅助函数 — CPM 计算（纯逻辑，不依赖实现）
# ============================================================
def _calc_cpm(total_casts: int, duration_seconds: float) -> float:
    """CPM = total_casts / (duration_seconds / 60)"""
    if duration_seconds <= 0:
        return 0.0
    return total_casts / (duration_seconds / 60.0)


def _calc_uptime_pct(
    uptime_ms: float, fight_duration_ms: float
) -> float:
    """uptime_pct = totalUptime_ms / fight_duration_ms * 100"""
    if fight_duration_ms <= 0:
        return 0.0
    return uptime_ms / fight_duration_ms * 100.0


# ============================================================
# 单元测试 — CPM 计算逻辑
# ============================================================
class TestSpellCounting:
    """施法计数和 CPM 计算测试。"""

    def test_cpm_calculation(self):
        """CPM = total_casts / (duration_seconds / 60)"""
        # 30 次施法 / 300 秒 = 6.0 CPM
        cpm = _calc_cpm(30, 300)
        assert cpm == 6.0

    def test_cpm_one_minute(self):
        """60 秒内 10 次施法 = 10.0 CPM"""
        cpm = _calc_cpm(10, 60)
        assert cpm == 10.0

    def test_cpm_long_fight(self):
        """600 秒内 120 次施法 = 12.0 CPM"""
        cpm = _calc_cpm(120, 600)
        assert cpm == 12.0

    def test_empty_casts(self):
        """无施法 → 0 CPM"""
        cpm = _calc_cpm(0, 300)
        assert cpm == 0.0

    def test_zero_duration(self):
        """0 秒战斗 → 0 CPM（防除零）"""
        cpm = _calc_cpm(10, 0)
        assert cpm == 0.0

    def test_negative_duration(self):
        """负数时长 → 0 CPM（防异常数据）"""
        cpm = _calc_cpm(10, -5)
        assert cpm == 0.0

    def test_fractional_cpm(self):
        """非整数 CPM: 7 次 / 120 秒 = 3.5 CPM"""
        cpm = _calc_cpm(7, 120)
        assert abs(cpm - 3.5) < 0.01


# ============================================================
# 单元测试 — Buff 覆盖率计算逻辑
# ============================================================
class TestBuffUptimes:
    """Buff 覆盖率提取测试。"""

    def test_uptime_percentage(self):
        """uptime_pct = totalUptime_ms / fight_duration_ms * 100"""
        # 150000ms uptime / 300000ms 战斗 = 50%
        pct = _calc_uptime_pct(150_000, 300_000)
        assert pct == 50.0

    def test_full_uptime(self):
        """100% 覆盖率"""
        pct = _calc_uptime_pct(300_000, 300_000)
        assert pct == 100.0

    def test_zero_uptime(self):
        """0% 覆盖率"""
        pct = _calc_uptime_pct(0, 300_000)
        assert pct == 0.0

    def test_zero_duration(self):
        """0 时长战斗 → 0% 覆盖率（防除零）"""
        pct = _calc_uptime_pct(100_000, 0)
        assert pct == 0.0

    def test_partial_uptime(self):
        """75% 覆盖率: 225000ms / 300000ms"""
        pct = _calc_uptime_pct(225_000, 300_000)
        assert pct == 75.0

    def test_over_100_uptime(self):
        """覆盖率可能超过 100%（多个 Buff 实例重叠）"""
        pct = _calc_uptime_pct(400_000, 300_000)
        assert pct > 100.0


# ============================================================
# 数据模型测试 — SpellStats
# ============================================================
class TestSpellStatsModel:
    """SpellStats 模型字段验证。"""

    def test_basic_construction(self):
        """基本构造和字段访问"""
        spell = SpellStats(
            name="Obliterate",
            spell_id=49020,
            total_casts=25.0,
            cpm=5.0,
            percentiles={"p25": 20.0, "p50": 25.0, "p75": 30.0},
        )
        assert spell.name == "Obliterate"
        assert spell.spell_id == 49020
        assert spell.total_casts == 25.0
        assert spell.cpm == 5.0
        assert spell.percentiles["p50"] == 25.0

    def test_serialization(self):
        """model_dump 序列化正确"""
        spell = SpellStats(
            name="Frost Strike",
            spell_id=49143,
            total_casts=18.0,
            cpm=3.6,
        )
        data = spell.model_dump()
        assert data["name"] == "Frost Strike"
        assert data["cpm"] == 3.6


# ============================================================
# 数据模型测试 — BuffUptime
# ============================================================
class TestBuffUptimeModel:
    """BuffUptime 模型字段验证。"""

    def test_basic_construction(self):
        """基本构造"""
        buff = BuffUptime(
            name="Pillar of Frost",
            spell_id=51271,
            uptime_pct=45.5,
        )
        assert buff.name == "Pillar of Frost"
        assert buff.spell_id == 51271
        assert buff.uptime_pct == 45.5


# ============================================================
# 数据模型测试 — RotationProfileResponse
# ============================================================
class TestRotationProfileResponseModel:
    """RotationProfileResponse 模型结构验证。"""

    def test_full_construction(self):
        """完整构造包含所有字段"""
        response = RotationProfileResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            sample_size=15,
            fight_duration_median=300.0,
            top_spells=[
                SpellStats(
                    name="Obliterate",
                    spell_id=49020,
                    total_casts=25.0,
                    cpm=5.0,
                    percentiles={"p25": 20.0, "p50": 25.0, "p75": 30.0},
                ),
                SpellStats(
                    name="Frost Strike",
                    spell_id=49143,
                    total_casts=18.0,
                    cpm=3.6,
                ),
            ],
            buff_uptimes=[
                BuffUptime(
                    name="Pillar of Frost",
                    spell_id=51271,
                    uptime_pct=45.5,
                ),
            ],
            dps_median=1_200_000,
            dps_p25=1_100_000,
            dps_p75=1_350_000,
        )
        assert response.spec == "frost-death-knight"
        assert response.encounter_id == 3001
        assert response.sample_size == 15
        assert response.fight_duration_median == 300.0
        assert len(response.top_spells) == 2
        assert response.top_spells[0].name == "Obliterate"
        assert len(response.buff_uptimes) == 1
        assert response.dps_median == 1_200_000

    def test_empty_response(self):
        """空响应（无数据）"""
        response = RotationProfileResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            difficulty="heroic",
            sample_size=0,
        )
        assert response.sample_size == 0
        assert response.top_spells == []
        assert response.buff_uptimes == []
        assert response.dps_median == 0.0

    def test_serialization_round_trip(self):
        """model_dump → 重建 → 字段一致"""
        original = RotationProfileResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            sample_size=10,
            fight_duration_median=280.0,
            top_spells=[
                SpellStats(
                    name="Obliterate",
                    spell_id=49020,
                    total_casts=25.0,
                    cpm=5.0,
                ),
            ],
            buff_uptimes=[
                BuffUptime(
                    name="Pillar of Frost",
                    spell_id=51271,
                    uptime_pct=45.5,
                ),
            ],
            dps_median=1_200_000,
        )
        data = original.model_dump()
        rebuilt = RotationProfileResponse(**data)
        assert rebuilt.spec == original.spec
        assert rebuilt.encounter_id == original.encounter_id
        assert rebuilt.sample_size == original.sample_size
        assert len(rebuilt.top_spells) == len(original.top_spells)
        assert rebuilt.top_spells[0].name == original.top_spells[0].name

    def test_dps_percentile_ordering(self):
        """DPS 百分位: p25 <= median <= p75"""
        response = RotationProfileResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            difficulty="heroic",
            dps_p25=1_000_000,
            dps_median=1_200_000,
            dps_p75=1_400_000,
        )
        assert response.dps_p25 <= response.dps_median <= response.dps_p75


# ============================================================
# 集成测试 — 完整流程（mock WCL）
#
# rotation 工具的查询流程:
#   1. characterRankings → 排行榜
#   2. fights(fightIDs: [...]) → 战斗时长 (每个 report)
#   3. masterData → 玩家 actor ID (每个 report)
#   4. events(dataType: Casts) → 施法事件 (每个 report)
#   5. table(dataType: Buffs) → Buff 覆盖率 (每个 report)
#
# MockWCLClient 使用最长匹配策略，需要为每种查询类型
# 配置不同的匹配键来区分。
# ============================================================

# 模拟 WCL 排行榜响应 — 用于 rotation profile
ROTATION_RANKINGS_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    {
                        "name": f"Player{i}",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": f"rpt_ROT{i:03d}", "fightID": 1},
                        "amount": 1_200_000 + i * 10_000,
                        "rank": i + 1,
                        "duration": 300_000,  # 300 秒（毫秒）
                    }
                    for i in range(5)
                ],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

# 模拟战斗信息响应 — fights query
ROTATION_FIGHT_INFO = {
    "reportData": {
        "report": {
            "fights": [
                {
                    "startTime": 100_000,
                    "endTime": 400_000,  # 300 秒战斗
                    "kill": True,
                }
            ]
        }
    },
}

# 模拟 masterData 响应 — 包含玩家 actor
def _make_master_data(player_name: str) -> dict:
    """为指定玩家生成 masterData 响应。"""
    return {
        "reportData": {
            "report": {
                "masterData": {
                    "actors": [
                        {
                            "id": 1,
                            "name": player_name,
                            "type": "Player",
                            "subType": "DeathKnight",
                        }
                    ]
                }
            }
        },
    }

# 模拟施法事件数据 — events(dataType: Casts)
ROTATION_CAST_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 105_000, "sourceID": 1},
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 115_000, "sourceID": 1},
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 125_000, "sourceID": 1},
                    {"type": "cast", "abilityGameID": 49143,
                     "ability": {"name": "Frost Strike"},
                     "timestamp": 110_000, "sourceID": 1},
                    {"type": "cast", "abilityGameID": 49143,
                     "ability": {"name": "Frost Strike"},
                     "timestamp": 120_000, "sourceID": 1},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# 模拟 Buff 覆盖率 — table(dataType: Buffs)
ROTATION_BUFF_TABLE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "auras": [
                        {
                            "name": "Pillar of Frost",
                            "abilityGameID": 51271,
                            "totalUptime": 150_000,
                            "totalUses": 5,
                        }
                    ]
                }
            }
        }
    },
}


def _setup_rotation_client() -> MockWCLClient:
    """
    为 rotation 集成测试预配置 mock WCL client。

    配置策略（利用最长匹配）:
    - "characterRankings" → 排行榜
    - "fights(fightIDs:" → 战斗信息
    - "masterData" → 每个 report 的 masterData（通用）
    - "dataType: Casts" → 施法事件
    - "dataType: Buffs" → Buff 覆盖率
    """
    client = MockWCLClient()
    client.set_response("characterRankings", ROTATION_RANKINGS_RESPONSE)
    client.set_response("fights(fightIDs:", ROTATION_FIGHT_INFO)
    # masterData — 每个 report 对应不同玩家名
    for i in range(5):
        code = f"rpt_ROT{i:03d}"
        client.set_response(
            f'report(code: "{code}") {{\n                masterData',
            _make_master_data(f"Player{i}"),
        )
    client.set_response("dataType: Casts", ROTATION_CAST_EVENTS)
    client.set_response("dataType: Buffs", ROTATION_BUFF_TABLE)
    return client


class TestRotationProfileIntegration:
    """完整流程集成测试（mock WCL）。"""

    @pytest.mark.asyncio
    async def test_basic_profile(self):
        """应返回有效的 RotationProfileResponse"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # 返回类型正确
        assert isinstance(result, RotationProfileResponse)
        # 基本字段
        assert result.spec == "frost-death-knight"
        assert result.encounter_id == 3001
        assert result.sample_size >= 1
        assert result.encounter_name == "Vorasius"

    @pytest.mark.asyncio
    async def test_has_spell_stats(self):
        """应返回非空的 top_spells 列表"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert len(result.top_spells) >= 1
        # 每个技能应有有效的 CPM 和施法次数
        for spell in result.top_spells:
            assert spell.cpm > 0
            assert spell.total_casts > 0
            assert len(spell.name) > 0

    @pytest.mark.asyncio
    async def test_fight_duration(self):
        """应返回合理的战斗时长中位数"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # mock 数据: startTime=100000, endTime=400000 → 300 秒
        assert result.fight_duration_median == 300.0

    @pytest.mark.asyncio
    async def test_dps_distribution(self):
        """应返回合理的 DPS 分布"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert result.dps_median > 0
        assert result.dps_p25 <= result.dps_median <= result.dps_p75

    @pytest.mark.asyncio
    async def test_empty_rankings(self):
        """无排名数据 → 优雅返回空结果"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = MockWCLClient()
        client.set_response("characterRankings", {
            "worldData": {
                "encounter": {
                    "name": "Vorasius",
                    "characterRankings": {
                        "rankings": [],
                        "page": 1,
                        "hasMorePages": False,
                    },
                }
            },
        })

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result, RotationProfileResponse)
        assert result.sample_size == 0
        assert result.top_spells == []

    @pytest.mark.asyncio
    async def test_top_spells_sorted_by_casts(self):
        """top_spells 按施法次数降序排列"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        if result.top_spells:
            casts = [s.total_casts for s in result.top_spells]
            assert casts == sorted(casts, reverse=True), \
                "top_spells 应按施法次数降序排列"

    @pytest.mark.asyncio
    async def test_cached_second_call(self):
        """相同参数第二次调用使用缓存"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        # 第一次调用
        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set") as mock_cset:
            await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )
            cached_data = mock_cset.call_args[0][1]

        count1 = client.query_call_count

        # 第二次调用（缓存命中）
        with patch("src.tools.rotation.cache_get", return_value=cached_data), \
             patch("src.tools.rotation.cache_set"):
            await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count2 = client.query_call_count
        assert count2 == count1, "缓存命中时不应有额外 WCL 查询"

    @pytest.mark.asyncio
    async def test_serialization_round_trip(self):
        """响应 model_dump → 重建 → 字段一致"""
        try:
            from src.tools.rotation import get_rotation_profile
        except ImportError:
            pytest.skip("src.tools.rotation 尚未实现")

        client = _setup_rotation_client()

        with patch("src.tools.rotation.cache_get", return_value=None), \
             patch("src.tools.rotation.cache_set"):
            result = await get_rotation_profile(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        data = result.model_dump()
        rebuilt = RotationProfileResponse(**data)
        assert rebuilt.spec == result.spec
        assert rebuilt.sample_size == result.sample_size
        assert len(rebuilt.top_spells) == len(result.top_spells)
