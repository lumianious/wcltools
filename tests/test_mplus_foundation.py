# ============================================================
# M+ 基础设施测试
# 覆盖: DIFFICULTY_MAP 扩展、M+ Pydantic 模型、keystone 字段、
#       query_mplus_rankings 函数（bracket 过滤、缓存、稀疏回退）
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import MockWCLClient
from tests.fixtures.wcl_responses import MPLUS_RANKINGS_RESPONSE


# ============================================================
# DIFFICULTY_MAP 测试
# ============================================================
class TestDifficultyMap:
    """DIFFICULTY_MAP 应包含 mythic_plus=10"""

    def test_mythic_plus_entry(self):
        """mythic_plus 映射到 difficulty=10"""
        from src.tools.builds import DIFFICULTY_MAP

        assert DIFFICULTY_MAP["mythic_plus"] == 10

    def test_no_regression_normal(self):
        """normal 仍然是 3"""
        from src.tools.builds import DIFFICULTY_MAP

        assert DIFFICULTY_MAP["normal"] == 3

    def test_no_regression_heroic(self):
        """heroic 仍然是 4"""
        from src.tools.builds import DIFFICULTY_MAP

        assert DIFFICULTY_MAP["heroic"] == 4

    def test_no_regression_mythic(self):
        """mythic 仍然是 5"""
        from src.tools.builds import DIFFICULTY_MAP

        assert DIFFICULTY_MAP["mythic"] == 5


# ============================================================
# MplusRankingEntry 模型测试
# ============================================================
class TestMplusRankingEntry:
    """MplusRankingEntry 应正确解析 WCL ranking 数据"""

    def test_parse_basic_fields(self):
        """基本字段解析"""
        from src.models import MplusRankingEntry

        entry = MplusRankingEntry(
            name="TestPlayer",
            amount=850000.5,
            duration=1920000,
            bracketData=12,
        )
        assert entry.name == "TestPlayer"
        assert entry.amount == 850000.5
        assert entry.duration == 1920000

    def test_bracket_data_alias(self):
        """bracketData 别名正确映射到 bracket_data"""
        from src.models import MplusRankingEntry

        entry = MplusRankingEntry(
            name="TestPlayer",
            amount=850000.5,
            duration=1920000,
            bracketData=12,
        )
        assert entry.bracket_data == 12

    def test_report_code_extraction(self):
        """report_code 可以直接设置"""
        from src.models import MplusRankingEntry

        entry = MplusRankingEntry(
            name="TestPlayer",
            amount=850000.5,
            duration=1920000,
            report_code="abc123mplus",
            fight_id=1,
        )
        assert entry.report_code == "abc123mplus"
        assert entry.fight_id == 1

    def test_class_name_alias(self):
        """class 别名正确映射到 class_name"""
        from src.models import MplusRankingEntry

        data = {
            "name": "TestPlayer",
            "class": "Mage",
            "spec": "Frost",
            "amount": 850000.5,
            "duration": 1920000,
            "bracketData": 12,
        }
        entry = MplusRankingEntry(**data)
        assert entry.class_name == "Mage"


# ============================================================
# MplusBenchmarkMeta 模型测试
# ============================================================
class TestMplusBenchmarkMeta:
    """MplusBenchmarkMeta 应验证必需字段"""

    def test_required_fields(self):
        """encounter_id, spec, key_level 为必需字段"""
        from src.models import MplusBenchmarkMeta

        meta = MplusBenchmarkMeta(
            encounter_id=112526,
            spec="frost-mage",
            key_level=12,
        )
        assert meta.encounter_id == 112526
        assert meta.spec == "frost-mage"
        assert meta.key_level == 12

    def test_optional_fields_defaults(self):
        """可选字段应有合理默认值"""
        from src.models import MplusBenchmarkMeta

        meta = MplusBenchmarkMeta(
            encounter_id=112526,
            spec="frost-mage",
            key_level=12,
        )
        assert meta.encounter_name == ""
        assert meta.actual_bracket == 0
        assert meta.sample_size == 0
        assert meta.median_dps == 0.0

    def test_missing_required_field_raises(self):
        """缺少必需字段应报错"""
        from src.models import MplusBenchmarkMeta

        with pytest.raises(Exception):
            MplusBenchmarkMeta(encounter_id=112526, spec="frost-mage")


# ============================================================
# M+ 排行榜 mock 数据测试
# ============================================================
class TestMplusFixtures:
    """M+ mock 数据应可导入"""

    def test_mplus_rankings_response_exists(self):
        """MPLUS_RANKINGS_RESPONSE 可导入"""
        rankings = (
            MPLUS_RANKINGS_RESPONSE["worldData"]["encounter"]
            ["characterRankings"]["rankings"]
        )
        assert len(rankings) == 2
        assert rankings[0]["bracketData"] == 12


# ============================================================
# query_mplus_rankings 函数测试
# ============================================================


def _make_mplus_rankings(
    count: int, bracket: int = 12, base_dps: float = 850000.0
) -> dict:
    """构造 M+ 排行榜响应，指定数量和 bracket"""
    rankings = []
    for i in range(count):
        rankings.append({
            "name": f"Player{i}",
            "class": "Mage",
            "spec": "Frost",
            "amount": base_dps - i * 10000,
            "duration": 1920000,
            "bracketData": bracket,
            "report": {"code": f"rpt{i:03d}", "fightID": i + 1},
        })
    return {
        "worldData": {
            "encounter": {
                "name": "Ara-Kara, City of Echoes",
                "characterRankings": {
                    "count": count,
                    "hasMorePages": False,
                    "rankings": rankings,
                },
            }
        }
    }


class TestQueryMplusRankings:
    """query_mplus_rankings 函数测试"""

    @pytest.mark.asyncio
    async def test_returns_entries(self):
        """应返回 MplusRankingEntry 列表"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5, bracket=12))

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert len(entries) == 5
        assert entries[0].name == "Player0"
        assert entries[0].bracket_data == 12

    @pytest.mark.asyncio
    async def test_difficulty_10_in_query(self):
        """应在 GraphQL 中传递 difficulty: 10"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5))

        queries: list[str] = []
        original_query = client.query

        async def capture_query(gql, **kw):
            queries.append(gql)
            return await original_query(gql, **kw)

        client.query = capture_query

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert any("difficulty: 10" in q for q in queries)

    @pytest.mark.asyncio
    async def test_bracket_in_query_when_provided(self):
        """key_level 提供时应在 GraphQL 中传递 bracket 参数"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5))

        queries: list[str] = []
        original_query = client.query

        async def capture_query(gql, **kw):
            queries.append(gql)
            return await original_query(gql, **kw)

        client.query = capture_query

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert any("bracket: 12" in q for q in queries)

    @pytest.mark.asyncio
    async def test_no_bracket_when_none(self):
        """key_level 为 None 时不应传递 bracket"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5))

        queries: list[str] = []
        original_query = client.query

        async def capture_query(gql, **kw):
            queries.append(gql)
            return await original_query(gql, **kw)

        client.query = capture_query

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=None
            )

        assert all("bracket:" not in q for q in queries)

    @pytest.mark.asyncio
    async def test_sample_size_limit(self):
        """默认 sample_size=5，应最多返回 5 条"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(10))

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert len(entries) == 5

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """缓存命中时不应查询 WCL"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        cached_data = {
            "meta": {
                "encounter_id": 112526,
                "encounter_name": "Ara-Kara",
                "spec": "frost-mage",
                "key_level": 12,
                "actual_bracket": 12,
                "sample_size": 2,
                "median_dps": 835000.0,
                "dps_p25": 820000.0,
                "dps_p75": 850000.0,
                "cached_at": "2026-03-28T00:00:00Z",
            },
            "entries": [
                {
                    "name": "CachedPlayer",
                    "amount": 850000.0,
                    "duration": 1920000,
                    "bracket_data": 12,
                    "report_code": "cached1",
                    "fight_id": 1,
                },
            ],
        }

        with patch("src.tools.mplus_rankings.cache_get", return_value=cached_data):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert entries[0].name == "CachedPlayer"
        assert client.query_call_count == 0

    @pytest.mark.asyncio
    async def test_cache_key_format(self):
        """缓存键格式: mplus_bench:{spec}:{encounter_id}:k{key_level}"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5))

        cache_keys: list[str] = []

        def mock_cache_get(key, ttl):
            cache_keys.append(key)
            return None

        with patch("src.tools.mplus_rankings.cache_get", side_effect=mock_cache_get), \
             patch("src.tools.mplus_rankings.cache_set"):
            await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert cache_keys[0] == "mplus_bench:frost-mage:112526:k12"

    @pytest.mark.asyncio
    async def test_sparse_bracket_fallback(self):
        """结果 < 3 时应尝试相邻 bracket +1 回退"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        # 第一次查询 bracket=12 返回 2 条（稀疏）
        # 第二次查询 bracket=13 返回 5 条（充足）
        call_count = [0]
        original_query = client.query

        async def mock_query(gql, **kw):
            call_count[0] += 1
            if "bracket: 12" in gql:
                return _make_mplus_rankings(2, bracket=12)
            if "bracket: 13" in gql:
                return _make_mplus_rankings(5, bracket=13)
            return _make_mplus_rankings(5, bracket=12)

        client.query = mock_query

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        # 应使用 fallback bracket=13 的结果
        assert meta.actual_bracket == 13
        assert len(entries) == 5

    @pytest.mark.asyncio
    async def test_sparse_fallback_discloses_actual_bracket(self):
        """稀疏回退时 MplusBenchmarkMeta.actual_bracket 应记录实际 bracket"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()

        async def mock_query(gql, **kw):
            if "bracket: 12" in gql:
                return _make_mplus_rankings(1, bracket=12)
            if "bracket: 13" in gql:
                return _make_mplus_rankings(1, bracket=13)
            if "bracket: 11" in gql:
                return _make_mplus_rankings(5, bracket=11)
            return _make_mplus_rankings(0)

        client.query = mock_query

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        # bracket=12 稀疏, bracket=13 也稀疏, bracket=11 充足
        assert meta.actual_bracket == 11
        assert meta.key_level == 12

    @pytest.mark.asyncio
    async def test_report_code_from_nested(self):
        """report_code 应从 ranking['report']['code'] 提取"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(3))

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert entries[0].report_code == "rpt000"
        assert entries[0].fight_id == 1

    @pytest.mark.asyncio
    async def test_meta_dps_stats(self):
        """MplusBenchmarkMeta 应包含 median_dps 统计"""
        from src.tools.mplus_rankings import query_mplus_rankings

        client = MockWCLClient()
        client.set_response("characterRankings", _make_mplus_rankings(5))

        with patch("src.tools.mplus_rankings.cache_get", return_value=None), \
             patch("src.tools.mplus_rankings.cache_set"):
            meta, entries = await query_mplus_rankings(
                client, encounter_id=112526, spec="frost-mage", key_level=12
            )

        assert meta.median_dps > 0
        assert meta.sample_size == 5
        assert meta.encounter_name == "Ara-Kara, City of Echoes"
