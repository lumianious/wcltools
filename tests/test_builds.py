# ============================================================
# get_top_builds 工具测试
# 覆盖天赋聚合、弹性节点、饰品、属性分布
#
# get_top_builds(client, spec, encounter_id, difficulty) -> TopBuildsResponse
# TopBuildsResponse 字段:
#   spec, encounter_id, encounter_name, difficulty, sample_size,
#   builds: list[TalentBuild], flex_nodes: list[FlexNode],
#   top_trinkets: list[TrinketInfo], stat_profile: StatProfile
#
# TalentBuild 字段: talent_import, usage_pct, player_count
# FlexNode 字段: talent_name, pick_rate
# TrinketInfo 字段: name, item_id, usage_pct, count
# StatProfile 只有 item_level（WCL characterRankings 不返回详细 stats）
# ============================================================
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import MockWCLClient
from tests.fixtures.expected_outputs import (
    EXPECTED_BUILD_A_USAGE_PCT,
    EXPECTED_BUILD_B_USAGE_PCT,
    EXPECTED_TRINKET_COUNTS,
    TOTAL_RANKINGS,
)
from tests.fixtures.wcl_responses import (
    CHARACTER_RANKINGS_RESPONSE,
)


# ============================================================
# 必需参数测试
# ============================================================
class TestBuildsParams:
    """get_top_builds 参数校验"""

    @pytest.mark.asyncio
    async def test_requires_spec_param(self, mock_wcl_client):
        """缺少 spec 参数应报错"""
        from src.tools.builds import get_top_builds

        with pytest.raises((TypeError, ValueError)):
            await get_top_builds(client=mock_wcl_client, encounter_id=3001)

    @pytest.mark.asyncio
    async def test_requires_encounter_id_param(self, mock_wcl_client):
        """缺少 encounter_id 参数应报错"""
        from src.tools.builds import get_top_builds

        with pytest.raises((TypeError, ValueError)):
            await get_top_builds(client=mock_wcl_client, spec="frost-death-knight")


# ============================================================
# 天赋构建聚合测试
# ============================================================
class TestBuildAggregation:
    """天赋构建使用率聚合"""

    @pytest.mark.asyncio
    async def test_aggregates_talent_builds(self, mock_wcl_client):
        """聚合天赋构建并按使用率排序"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert len(result.builds) >= 2  # 至少有两种构建
        # 按使用率降序排列
        usages = [b.usage_pct for b in result.builds]
        assert usages == sorted(usages, reverse=True)

    @pytest.mark.asyncio
    async def test_build_a_is_most_popular(self, mock_wcl_client):
        """天赋 A（Obliteration）应是最热门构建"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        top_build = result.builds[0]
        # talent_import 是 "talentID:points,..." 格式
        assert len(top_build.talent_import) > 5
        # 使用率约 66.7%，允许浮点误差
        assert abs(top_build.usage_pct - EXPECTED_BUILD_A_USAGE_PCT) < 1.0

    @pytest.mark.asyncio
    async def test_each_build_has_talent_import(self, mock_wcl_client):
        """每种构建包含可比较的天赋字符串"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        for build in result.builds:
            assert isinstance(build.talent_import, str)
            assert len(build.talent_import) > 5

    @pytest.mark.asyncio
    async def test_each_build_has_player_count(self, mock_wcl_client):
        """每种构建包含玩家数"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        total_players = sum(b.player_count for b in result.builds)
        assert total_players == TOTAL_RANKINGS

    @pytest.mark.asyncio
    async def test_usage_pct_sums_to_100(self, mock_wcl_client):
        """所有构建使用率之和为 100%"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        total = sum(b.usage_pct for b in result.builds)
        assert abs(total - 100.0) < 0.5


# ============================================================
# 弹性节点测试
# ============================================================
class TestFlexNodes:
    """天赋弹性节点（构建分歧点）"""

    @pytest.mark.asyncio
    async def test_identifies_flex_nodes(self, mock_wcl_client):
        """识别天赋树中构建分歧的节点"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # flex_nodes 应存在（因为有多种构建）
        assert isinstance(result.flex_nodes, list)

    @pytest.mark.asyncio
    async def test_flex_nodes_have_pick_rates(self, mock_wcl_client):
        """弹性节点包含选择率"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        if result.flex_nodes:
            for node in result.flex_nodes:
                assert hasattr(node, "talent_name")
                assert hasattr(node, "pick_rate")
                assert 0 < node.pick_rate <= 100


# ============================================================
# 饰品分析测试
# ============================================================
class TestTrinketAnalysis:
    """饰品使用率分析"""

    @pytest.mark.asyncio
    async def test_extracts_top_trinkets(self, mock_wcl_client):
        """提取饰品使用率排行"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result.top_trinkets, list)
        assert len(result.top_trinkets) >= 2

    @pytest.mark.asyncio
    async def test_trinkets_have_usage_pct(self, mock_wcl_client):
        """每个饰品包含使用率百分比"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        for trinket in result.top_trinkets:
            assert isinstance(trinket.name, str)
            assert 0 < trinket.usage_pct <= 100

    @pytest.mark.asyncio
    async def test_void_catalyst_is_most_used(self, mock_wcl_client):
        """Void-Touched Catalyst 应是使用率最高的饰品"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        top_trinket = result.top_trinkets[0]
        assert top_trinket.name == "Void-Touched Catalyst"
        # 预期使用率 11/15 ~ 73.3%
        expected_pct = (
            EXPECTED_TRINKET_COUNTS["Void-Touched Catalyst"]
            / TOTAL_RANKINGS
            * 100
        )
        assert abs(top_trinket.usage_pct - expected_pct) < 2.0


# ============================================================
# 属性分布测试
# ============================================================
class TestStatProfile:
    """属性分布统计 — 仅包含 item_level（WCL 限制）"""

    @pytest.mark.asyncio
    async def test_stat_profile_has_item_level(self, mock_wcl_client):
        """stat_profile 包含 item_level 分布"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # stat_profile 应有 item_level
        assert result.stat_profile is not None
        ilvl = result.stat_profile.item_level
        assert ilvl.median > 0

    @pytest.mark.asyncio
    async def test_item_level_p25_leq_median_leq_p75(self, mock_wcl_client):
        """item_level: P25 <= 中位数 <= P75"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            result = await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        ilvl = result.stat_profile.item_level
        assert ilvl.p25 <= ilvl.median <= ilvl.p75


# ============================================================
# 缓存行为测试
# ============================================================
class TestBuildsCaching:
    """get_top_builds 缓存机制"""

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self, mock_wcl_client):
        """相同参数的第二次调用使用缓存"""
        from src.tools.builds import get_top_builds

        # 第一次调用
        with patch("src.tools.builds.cache_get", return_value=None) as mock_cget, \
             patch("src.tools.builds.cache_set") as mock_cset:
            await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )
            cached_data = mock_cset.call_args[0][1]

        count1 = mock_wcl_client.query_call_count

        # 第二次调用（cache hit）
        with patch("src.tools.builds.cache_get", return_value=cached_data), \
             patch("src.tools.builds.cache_set"):
            await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count2 = mock_wcl_client.query_call_count
        assert count2 == count1

    @pytest.mark.asyncio
    async def test_different_encounter_not_cached(self, mock_wcl_client):
        """不同 encounter_id 不共享缓存"""
        from src.tools.builds import get_top_builds

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count1 = mock_wcl_client.query_call_count

        with patch("src.tools.builds.cache_get", return_value=None), \
             patch("src.tools.builds.cache_set"):
            await get_top_builds(
                client=mock_wcl_client,
                spec="frost-death-knight",
                encounter_id=3002,
            )

        count2 = mock_wcl_client.query_call_count
        assert count2 > count1
