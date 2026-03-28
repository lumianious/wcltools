# ============================================================
# M+ 基础设施测试
# 覆盖: DIFFICULTY_MAP 扩展、M+ Pydantic 模型、keystone 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest

from tests.conftest import MockWCLClient


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
        from tests.fixtures.wcl_responses import MPLUS_RANKINGS_RESPONSE

        rankings = (
            MPLUS_RANKINGS_RESPONSE["worldData"]["encounter"]
            ["characterRankings"]["rankings"]
        )
        assert len(rankings) == 2
        assert rankings[0]["bracketData"] == 12
