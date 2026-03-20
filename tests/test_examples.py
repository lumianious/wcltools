# ============================================================
# get_example_logs 工具测试
# 覆盖 URL 构造、时长转换、匿名过滤、空排名、集成流程
#
# 测试目标模块: src.tools.examples (Phase 5)
# 数据模型: ExampleLog, ExampleLogsResponse
#
# 测试策略:
#   - 纯单元测试: URL 格式、时长转换（不依赖实现）
#   - 集成测试: 通过 mock WCL client 测试完整流程
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.examples import (
    ExampleLog,
    ExampleLogsResponse,
    WCL_REPORT_BASE,
    get_example_logs,
)
from tests.conftest import MockWCLClient


# ============================================================
# 单元测试 — URL 构造与时长转换
# ============================================================
class TestExampleLogs:
    """URL 格式和数据转换测试。"""

    def test_url_construction(self):
        """URL 格式: https://www.warcraftlogs.com/reports/{code}#fight={fid}"""
        code = "rpt_EX001"
        fight_id = 3
        expected = f"{WCL_REPORT_BASE}/{code}#fight={fight_id}"
        assert expected == "https://www.warcraftlogs.com/reports/rpt_EX001#fight=3"

    def test_url_base_constant(self):
        """WCL_REPORT_BASE 应指向正确域名"""
        assert "warcraftlogs.com" in WCL_REPORT_BASE
        assert WCL_REPORT_BASE.startswith("https://")

    def test_duration_conversion(self):
        """毫秒 → 秒转换"""
        duration_ms = 300_000
        duration_sec = round(duration_ms / 1000.0, 1)
        assert duration_sec == 300.0

    def test_duration_conversion_fractional(self):
        """非整数毫秒 → 秒（保留 1 位小数）"""
        duration_ms = 312_345
        duration_sec = round(duration_ms / 1000.0, 1)
        assert duration_sec == 312.3

    def test_duration_conversion_zero(self):
        """0 毫秒 → 0 秒"""
        duration_sec = round(0 / 1000.0, 1)
        assert duration_sec == 0.0

    def test_example_log_model(self):
        """ExampleLog 模型基本构造"""
        log = ExampleLog(
            url="https://www.warcraftlogs.com/reports/ABC123#fight=1",
            report_code="ABC123",
            fight_id=1,
            player_name="Frostblade",
            dps=1_350_000.5,
            fight_duration=300.0,
            rank=1,
        )
        assert log.url == "https://www.warcraftlogs.com/reports/ABC123#fight=1"
        assert log.report_code == "ABC123"
        assert log.fight_id == 1
        assert log.player_name == "Frostblade"
        assert log.dps == 1_350_000.5
        assert log.fight_duration == 300.0
        assert log.rank == 1

    def test_example_logs_response_model(self):
        """ExampleLogsResponse 模型构造"""
        response = ExampleLogsResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            logs=[
                ExampleLog(
                    url="https://www.warcraftlogs.com/reports/ABC#fight=1",
                    report_code="ABC",
                    fight_id=1,
                    player_name="Player1",
                    dps=1_000_000,
                    fight_duration=300.0,
                    rank=1,
                ),
            ],
        )
        assert response.spec == "frost-death-knight"
        assert response.encounter_id == 3001
        assert len(response.logs) == 1
        assert response.logs[0].player_name == "Player1"

    def test_serialization_round_trip(self):
        """model_dump → 重建 → 字段一致"""
        original = ExampleLogsResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            logs=[
                ExampleLog(
                    url="https://www.warcraftlogs.com/reports/XYZ#fight=2",
                    report_code="XYZ",
                    fight_id=2,
                    player_name="TestPlayer",
                    dps=900_000,
                    fight_duration=280.0,
                    rank=5,
                ),
            ],
        )
        data = original.model_dump()
        rebuilt = ExampleLogsResponse(**data)
        assert rebuilt.spec == original.spec
        assert rebuilt.encounter_id == original.encounter_id
        assert len(rebuilt.logs) == len(original.logs)
        assert rebuilt.logs[0].player_name == original.logs[0].player_name


# ============================================================
# 模拟 WCL 排行榜数据
# ============================================================
EXAMPLE_RANKINGS_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    {
                        "name": f"Player{i}",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": f"rpt_EX{i:03d}", "fightID": 1},
                        "amount": 1_350_000 - i * 10_000,
                        "rank": i + 1,
                        "duration": 300_000,  # 300 秒（毫秒）
                    }
                    for i in range(8)
                ],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

# 包含匿名玩家的排行榜
EXAMPLE_RANKINGS_WITH_ANONYMOUS = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    {
                        "name": "Anonymous",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_ANON1", "fightID": 1},
                        "amount": 1_400_000,
                        "rank": 1,
                        "duration": 300_000,
                    },
                    {
                        "name": "Frostblade",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_REAL1", "fightID": 1},
                        "amount": 1_350_000,
                        "rank": 2,
                        "duration": 290_000,
                    },
                    {
                        "name": "",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_EMPTY", "fightID": 1},
                        "amount": 1_300_000,
                        "rank": 3,
                        "duration": 310_000,
                    },
                    {
                        "name": "IceLord",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_REAL2", "fightID": 2},
                        "amount": 1_280_000,
                        "rank": 4,
                        "duration": 320_000,
                    },
                    {
                        "name": "anonymous",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_ANON2", "fightID": 1},
                        "amount": 1_250_000,
                        "rank": 5,
                        "duration": 300_000,
                    },
                    {
                        "name": "FrostKnight",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": "rpt_REAL3", "fightID": 3},
                        "amount": 1_200_000,
                        "rank": 6,
                        "duration": 280_000,
                    },
                ],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

# 空排行榜
EXAMPLE_RANKINGS_EMPTY = {
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
}


# ============================================================
# 集成测试 — get_example_logs 完整流程
# ============================================================
class TestExampleLogsIntegration:
    """get_example_logs 端到端集成测试（mock WCL）。"""

    @pytest.mark.asyncio
    async def test_basic_examples(self):
        """应返回 5 条日志（默认 count=5）"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result, ExampleLogsResponse)
        assert result.spec == "frost-death-knight"
        assert result.encounter_id == 3001
        assert result.encounter_name == "Vorasius"
        assert result.difficulty == "heroic"
        assert len(result.logs) == 5

        # 验证每条日志的 URL 格式
        for log in result.logs:
            assert log.url.startswith(WCL_REPORT_BASE)
            assert "#fight=" in log.url
            assert log.player_name
            assert log.dps > 0
            assert log.fight_duration > 0

    @pytest.mark.asyncio
    async def test_url_format(self):
        """验证日志 URL 正确拼接 report_code 和 fight_id"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=3,
            )

        for log in result.logs:
            expected_url = f"{WCL_REPORT_BASE}/{log.report_code}#fight={log.fight_id}"
            assert log.url == expected_url

    @pytest.mark.asyncio
    async def test_duration_in_seconds(self):
        """fight_duration 应为秒（WCL 返回毫秒）"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=3,
            )

        for log in result.logs:
            # mock 数据 duration=300_000ms → 300.0s
            assert log.fight_duration == 300.0

    @pytest.mark.asyncio
    async def test_filters_anonymous(self):
        """匿名玩家（Anonymous / 空名）应被过滤"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_WITH_ANONYMOUS)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=5,
            )

        # 排行中有 3 个匿名/空名 + 3 个实名，count=5 但只有 3 个实名
        assert len(result.logs) == 3
        player_names = [log.player_name for log in result.logs]
        assert "Anonymous" not in player_names
        assert "anonymous" not in player_names
        assert "" not in player_names
        # 实名玩家应全部存在
        assert "Frostblade" in player_names
        assert "IceLord" in player_names
        assert "FrostKnight" in player_names

    @pytest.mark.asyncio
    async def test_empty_rankings(self):
        """无排名数据 → 空日志列表"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_EMPTY)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result, ExampleLogsResponse)
        assert result.logs == []
        assert result.encounter_name == "Vorasius"

    @pytest.mark.asyncio
    async def test_count_clamped_min(self):
        """count < 3 → 强制为 3"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=1,
            )

        # count 被 clamp 到 3
        assert len(result.logs) == 3

    @pytest.mark.asyncio
    async def test_count_clamped_max(self):
        """count > 5 → 强制为 5"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=10,
            )

        # count 被 clamp 到 5
        assert len(result.logs) == 5

    @pytest.mark.asyncio
    async def test_cached_second_call(self):
        """相同参数第二次调用使用缓存"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        # 第一次调用
        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set") as mock_cset:
            await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )
            cached_data = mock_cset.call_args[0][1]

        count1 = client.query_call_count

        # 第二次调用（缓存命中）
        with patch("src.tools.examples.cache_get", return_value=cached_data), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count2 = client.query_call_count
        assert count2 == count1, "缓存命中时不应有额外 WCL 查询"
        assert isinstance(result, ExampleLogsResponse)
        assert len(result.logs) == 5

    @pytest.mark.asyncio
    async def test_dps_values_from_amount(self):
        """DPS 取自 rankings 的 amount 字段"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                count=3,
            )

        # 第一个玩家 Player0: amount=1_350_000
        first_log = result.logs[0]
        assert first_log.dps == 1_350_000.0

    @pytest.mark.asyncio
    async def test_invalid_spec_raises(self):
        """无效 spec slug → 报错"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            with pytest.raises(ValueError, match="无法解析 spec"):
                await get_example_logs(
                    client=client,
                    spec="invalid-nonexistent-spec",
                    encounter_id=3001,
                )

    @pytest.mark.asyncio
    async def test_mythic_difficulty(self):
        """mythic 难度应正常工作"""
        client = MockWCLClient()
        client.set_response("characterRankings", EXAMPLE_RANKINGS_RESPONSE)

        with patch("src.tools.examples.cache_get", return_value=None), \
             patch("src.tools.examples.cache_set"):
            result = await get_example_logs(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                difficulty="mythic",
            )

        assert result.difficulty == "mythic"
        assert len(result.logs) == 5
