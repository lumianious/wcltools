# ============================================================
# _wcl_helpers 共享工具函数测试
# 覆盖报告解析、玩家匹配、战斗信息查询
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest

from tests.conftest import MockWCLClient
from src.tools._wcl_helpers import (
    extract_report_code,
    find_actor_id_ci,
    query_fight_info_full,
)


# ============================================================
# URL / Report Code 解析测试
# ============================================================
class TestExtractReportCode:
    """extract_report_code: 从 URL 或纯字符串提取 report code"""

    def test_plain_code(self):
        """纯 report code 直接返回"""
        assert extract_report_code("ABC123") == "ABC123"

    def test_full_url(self):
        """完整 WCL URL 提取 report code"""
        url = "https://www.warcraftlogs.com/reports/ABC123"
        assert extract_report_code(url) == "ABC123"

    def test_url_with_fragment(self):
        """带 fragment 的 URL 正确提取 code"""
        url = "https://www.warcraftlogs.com/reports/XyZ789#fight=3"
        assert extract_report_code(url) == "XyZ789"

    def test_url_with_query(self):
        """带 query 参数的 URL 正确提取 code"""
        url = "https://www.warcraftlogs.com/reports/QwErTy?foo=bar"
        assert extract_report_code(url) == "QwErTy"

    def test_whitespace(self):
        """前后空白被 strip 后返回"""
        assert extract_report_code("  ABC123  ") == "ABC123"

    def test_empty_string(self):
        """空字符串返回空字符串"""
        assert extract_report_code("") == ""


# ============================================================
# 玩家名称匹配测试（大小写不敏感）
# ============================================================
class TestFindActorIdCi:
    """find_actor_id_ci: 大小写不敏感的玩家查找"""

    _ACTORS = [
        {"name": "TestPlayer", "id": 1},
        {"name": "TankBro", "id": 2},
        {"name": "HealerGirl", "id": 3},
    ]

    def test_exact_match(self):
        """精确大小写匹配返回正确 id"""
        result = find_actor_id_ci(self._ACTORS, "TestPlayer")
        assert result == 1

    def test_case_insensitive(self):
        """全小写输入匹配到 mixed-case 玩家"""
        result = find_actor_id_ci(self._ACTORS, "testplayer")
        assert result == 1

    def test_not_found(self):
        """不存在的玩家返回 None"""
        result = find_actor_id_ci(self._ACTORS, "NoSuchPlayer")
        assert result is None

    def test_empty_actors(self):
        """空列表返回 None"""
        result = find_actor_id_ci([], "TestPlayer")
        assert result is None


# ============================================================
# WCL 战斗信息查询测试
# ============================================================
class TestQueryFightInfoFull:
    """query_fight_info_full: 查询指定战斗的完整信息"""

    @pytest.mark.asyncio
    async def test_returns_fight_info(self):
        """正常响应返回第一条战斗信息 dict"""
        client = MockWCLClient()
        client.set_response("fights", {
            "reportData": {
                "report": {
                    "fights": [
                        {
                            "startTime": 1000,
                            "endTime": 5000,
                            "kill": True,
                            "encounterID": 2820,
                            "name": "Ky'veza",
                        }
                    ]
                }
            }
        })

        result = await query_fight_info_full(client, "ABC123", 1)
        assert isinstance(result, dict)
        assert result["encounterID"] == 2820
        assert result["name"] == "Ky'veza"

    @pytest.mark.asyncio
    async def test_returns_empty_dict(self):
        """fights 为空列表时返回空 dict"""
        client = MockWCLClient()
        client.set_response("fights", {
            "reportData": {
                "report": {
                    "fights": []
                }
            }
        })

        result = await query_fight_info_full(client, "ABC123", 99)
        assert result == {}

    @pytest.mark.asyncio
    async def test_fight_fields(self):
        """返回的 dict 包含所有预期字段"""
        client = MockWCLClient()
        client.set_response("fights", {
            "reportData": {
                "report": {
                    "fights": [
                        {
                            "startTime": 0,
                            "endTime": 3000,
                            "kill": False,
                            "encounterID": 2921,
                            "name": "Test Boss",
                        }
                    ]
                }
            }
        })

        result = await query_fight_info_full(client, "XYZ456", 2)
        expected_keys = {"startTime", "endTime", "kill", "encounterID", "name"}
        assert expected_keys == set(result.keys())
