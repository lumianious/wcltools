# ============================================================
# get_encounters 工具测试
# 覆盖内容过滤、缓存行为、空数据处理
#
# get_encounters(client, content_type) -> EncountersResponse
# EncountersResponse 有 expansion: str, zones: list[Zone]
# ============================================================
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import MockWCLClient
from tests.fixtures.wcl_responses import (
    WORLD_DATA_EMPTY_RESPONSE,
    WORLD_DATA_RESPONSE,
    WORLD_DATA_WITH_DUNGEONS_RESPONSE,
)


# ============================================================
# 正常返回测试
# ============================================================
class TestEncountersRetrieval:
    """get_encounters 数据获取与过滤"""

    @pytest.mark.asyncio
    async def test_returns_all_zones_when_no_filter(self, mock_wcl_client):
        """不传 content_type 时返回所有区域"""
        from src.tools.encounters import get_encounters

        # 禁用缓存，避免跨测试干扰
        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client)

        assert len(result.zones) == 3
        zone_names = [z.name for z in result.zones]
        assert "The Voidspire" in zone_names
        assert "Dreamrift" in zone_names
        assert "March on Quel'Danas" in zone_names

    @pytest.mark.asyncio
    async def test_returns_all_zones_when_filter_all(self, mock_wcl_client):
        """content_type="all" 返回所有区域"""
        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client, content_type="all")

        assert len(result.zones) == 3

    @pytest.mark.asyncio
    async def test_returns_raid_zones_only(self, mock_wcl_client):
        """content_type="raid" 只返回团本区域（3+ Boss）"""
        # 使用含大秘境数据的响应
        mock_wcl_client.set_response("worldData", WORLD_DATA_WITH_DUNGEONS_RESPONSE)

        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client, content_type="raid")

        zone_names = [z.name for z in result.zones]
        # 团本（4 个 Boss）应包含
        assert "Algeth'ar Academy" in zone_names
        assert "Magisters' Terrace" in zone_names
        # 地下城（2 个 Boss）不应包含
        assert "The Voidspire" not in zone_names

    @pytest.mark.asyncio
    async def test_returns_mythic_plus_zones_only(self, mock_wcl_client):
        """content_type="mythic_plus" 只返回大秘境区域（1-2 Boss）"""
        mock_wcl_client.set_response("worldData", WORLD_DATA_WITH_DUNGEONS_RESPONSE)

        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(
                client=mock_wcl_client, content_type="mythic_plus"
            )

        zone_names = [z.name for z in result.zones]
        # 地下城（2 Boss）应包含
        assert "The Voidspire" in zone_names
        # 团本（4 Boss）不应包含
        assert "Algeth'ar Academy" not in zone_names
        assert "Magisters' Terrace" not in zone_names

    @pytest.mark.asyncio
    async def test_each_zone_has_encounters(self, mock_wcl_client):
        """每个区域包含 encounters 列表"""
        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client)

        for zone in result.zones:
            assert zone.id > 0
            assert isinstance(zone.name, str)
            assert isinstance(zone.encounters, list)
            assert len(zone.encounters) > 0

    @pytest.mark.asyncio
    async def test_each_encounter_has_id_and_name(self, mock_wcl_client):
        """每个 encounter 有 id 和 name 字段"""
        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client)

        for zone in result.zones:
            for encounter in zone.encounters:
                assert isinstance(encounter.id, int)
                assert isinstance(encounter.name, str)

    @pytest.mark.asyncio
    async def test_has_expansion_name(self, mock_wcl_client):
        """返回值包含资料片名称"""
        from src.tools.encounters import get_encounters

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client)

        assert result.expansion == "Midnight"

    @pytest.mark.asyncio
    async def test_handles_empty_zones_with_known_expansion(self, mock_wcl_client):
        """资料片存在但区域为空时返回空列表"""
        from src.tools.encounters import get_encounters

        # 模拟: 发现资料片成功（第一次查询返回有数据），
        # 但最终完整查询返回空区域
        call_count = 0
        original_query = mock_wcl_client.query

        async def query_side_effect(graphql, *, with_rate_limit=True):
            nonlocal call_count
            call_count += 1
            # 第一次调用（_discover）: 返回有区域的数据以通过发现
            if call_count == 1:
                return dict(WORLD_DATA_RESPONSE)
            # 第二次调用（完整查询）: 返回空区域
            return dict(WORLD_DATA_EMPTY_RESPONSE)

        mock_wcl_client.query = query_side_effect

        with patch("src.tools.encounters.cache_get", return_value=None), \
             patch("src.tools.encounters.cache_set"):
            result = await get_encounters(client=mock_wcl_client)

        assert result.zones == []


# ============================================================
# 缓存行为测试
# ============================================================
class TestEncountersCaching:
    """get_encounters 缓存机制"""

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self, mock_wcl_client):
        """第二次调用应使用缓存，不再查询 WCL"""
        from src.tools.encounters import get_encounters

        # 第一次调用（cache miss，写入缓存）
        with patch("src.tools.encounters.cache_get", return_value=None) as mock_cget, \
             patch("src.tools.encounters.cache_set") as mock_cset:
            result1 = await get_encounters(client=mock_wcl_client)
            # 获取 cache_set 写入的数据
            assert mock_cset.called
            cached_data = mock_cset.call_args[0][1]

        call_count_after_first = mock_wcl_client.query_call_count

        # 第二次调用（cache hit）
        with patch("src.tools.encounters.cache_get", return_value=cached_data), \
             patch("src.tools.encounters.cache_set"):
            result2 = await get_encounters(client=mock_wcl_client)

        call_count_after_second = mock_wcl_client.query_call_count

        # 数据应一致（比较 model_dump）
        assert result1.model_dump() == result2.model_dump()
        # 第二次不应增加查询次数
        assert call_count_after_second == call_count_after_first
