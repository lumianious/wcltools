# ============================================================
# Pydantic 模型验证测试
# 覆盖正确数据验证、无效数据拒绝、序列化格式
#
# 模型清单（与 src/models.py 对齐）:
#   Encounter, Zone, EncountersResponse
#   TalentBuild, FlexNode, TrinketInfo, StatDistribution, StatProfile
#   TopBuildsResponse
#   RateLimitInfo
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError


# ============================================================
# Encounter 模型测试
# ============================================================
class TestEncounterModel:
    """Encounter 数据模型"""

    def test_valid_encounter(self):
        """有效 encounter 数据通过验证"""
        from src.models import Encounter

        enc = Encounter(id=3001, name="Vorasius")
        assert enc.id == 3001
        assert enc.name == "Vorasius"

    def test_encounter_rejects_missing_id(self):
        """缺少 id 被拒绝"""
        from src.models import Encounter

        with pytest.raises(ValidationError):
            Encounter(name="Vorasius")  # type: ignore

    def test_encounter_rejects_missing_name(self):
        """缺少 name 被拒绝"""
        from src.models import Encounter

        with pytest.raises(ValidationError):
            Encounter(id=3001)  # type: ignore

    def test_encounter_rejects_string_id(self):
        """id 不接受非数字类型"""
        from src.models import Encounter

        with pytest.raises(ValidationError):
            Encounter(id="not_a_number", name="Test")  # type: ignore

    def test_encounter_serialization(self):
        """序列化为 dict 格式正确"""
        from src.models import Encounter

        enc = Encounter(id=3001, name="Vorasius")
        data = enc.model_dump()
        assert data == {"id": 3001, "name": "Vorasius"}


# ============================================================
# Zone 模型测试
# ============================================================
class TestZoneModel:
    """Zone 数据模型"""

    def test_valid_zone_with_encounters(self):
        """含 encounters 的有效 zone 通过验证"""
        from src.models import Encounter, Zone

        zone = Zone(
            id=100,
            name="The Voidspire",
            encounters=[
                Encounter(id=3001, name="Vorasius"),
                Encounter(id=3002, name="Darkweaver"),
            ],
        )
        assert zone.id == 100
        assert len(zone.encounters) == 2

    def test_zone_with_empty_encounters(self):
        """encounters 为空列表也有效"""
        from src.models import Zone

        zone = Zone(id=100, name="The Voidspire", encounters=[])
        assert zone.encounters == []

    def test_zone_defaults_to_empty_encounters(self):
        """不传 encounters 时默认为空列表"""
        from src.models import Zone

        zone = Zone(id=100, name="The Voidspire")
        assert zone.encounters == []

    def test_zone_serialization(self):
        """序列化嵌套 encounters"""
        from src.models import Encounter, Zone

        zone = Zone(
            id=100,
            name="The Voidspire",
            encounters=[Encounter(id=3001, name="Vorasius")],
        )
        data = zone.model_dump()
        assert data == {
            "id": 100,
            "name": "The Voidspire",
            "encounters": [{"id": 3001, "name": "Vorasius"}],
        }


# ============================================================
# TalentBuild 模型测试
# ============================================================
class TestTalentBuildModel:
    """TalentBuild 数据模型"""

    def test_valid_talent_build(self):
        """有效天赋构建数据通过验证"""
        from src.models import TalentBuild

        build = TalentBuild(
            talent_import="96161:2,96162:1,96163:1",
            usage_pct=66.7,
            player_count=10,
        )
        assert build.usage_pct == 66.7
        assert build.player_count == 10

    def test_talent_build_requires_talent_import(self):
        """缺少 talent_import 被拒绝"""
        from src.models import TalentBuild

        with pytest.raises(ValidationError):
            TalentBuild(usage_pct=66.7, player_count=10)  # type: ignore

    def test_talent_build_requires_usage_pct(self):
        """缺少 usage_pct 被拒绝"""
        from src.models import TalentBuild

        with pytest.raises(ValidationError):
            TalentBuild(talent_import="ABC", player_count=10)  # type: ignore


# ============================================================
# TrinketInfo 模型测试
# ============================================================
class TestTrinketInfoModel:
    """TrinketInfo 数据模型"""

    def test_valid_trinket_info(self):
        """有效饰品数据通过验证"""
        from src.models import TrinketInfo

        trinket = TrinketInfo(
            name="Void-Touched Catalyst",
            item_id=220305,
            usage_pct=73.3,
            count=11,
        )
        assert trinket.item_id == 220305
        assert trinket.name == "Void-Touched Catalyst"

    def test_trinket_info_rejects_missing_name(self):
        """缺少 name 被拒绝"""
        from src.models import TrinketInfo

        with pytest.raises(ValidationError):
            TrinketInfo(item_id=220305, usage_pct=73.3)  # type: ignore

    def test_trinket_info_defaults(self):
        """item_id 和 count 有默认值"""
        from src.models import TrinketInfo

        trinket = TrinketInfo(name="Test Trinket", usage_pct=50.0)
        assert trinket.item_id == 0
        assert trinket.count == 0


# ============================================================
# StatDistribution / StatProfile 模型测试
# ============================================================
class TestStatProfileModel:
    """StatProfile（百分位属性分布）模型"""

    def test_valid_stat_distribution(self):
        """有效属性分布数据通过验证"""
        from src.models import StatDistribution

        dist = StatDistribution(median=622.0, p25=618.0, p75=625.0)
        assert dist.median == 622.0
        assert dist.p25 == 618.0
        assert dist.p75 == 625.0

    def test_stat_distribution_defaults(self):
        """StatDistribution 全部字段有默认值 0.0"""
        from src.models import StatDistribution

        dist = StatDistribution()
        assert dist.median == 0.0
        assert dist.p25 == 0.0
        assert dist.p75 == 0.0

    def test_stat_profile_defaults(self):
        """StatProfile 各属性有默认的空 StatDistribution"""
        from src.models import StatProfile

        profile = StatProfile()
        assert profile.item_level.median == 0.0
        assert profile.crit.median == 0.0

    def test_stat_profile_serialization(self):
        """属性分布序列化格式正确"""
        from src.models import StatDistribution, StatProfile

        profile = StatProfile(
            item_level=StatDistribution(median=622.0, p25=618.0, p75=625.0),
        )
        data = profile.model_dump()
        assert data["item_level"]["median"] == 622.0
        assert data["item_level"]["p75"] == 625.0


# ============================================================
# TopBuildsResponse 模型测试
# ============================================================
class TestTopBuildsResponseModel:
    """TopBuildsResponse（get_top_builds 返回值）模型"""

    def test_valid_full_result(self):
        """完整的 top_builds 返回值通过验证"""
        from src.models import (
            FlexNode,
            StatDistribution,
            StatProfile,
            TalentBuild,
            TopBuildsResponse,
            TrinketInfo,
        )

        result = TopBuildsResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            sample_size=15,
            builds=[
                TalentBuild(
                    talent_import="96161:2,96162:1",
                    usage_pct=66.7,
                    player_count=10,
                ),
                TalentBuild(
                    talent_import="96170:1,96171:2",
                    usage_pct=26.7,
                    player_count=4,
                ),
            ],
            flex_nodes=[
                FlexNode(talent_name="TalentID 96163", pick_rate=66.7),
            ],
            top_trinkets=[
                TrinketInfo(
                    name="Void-Touched Catalyst",
                    item_id=220305,
                    usage_pct=73.3,
                    count=11,
                ),
            ],
            stat_profile=StatProfile(
                item_level=StatDistribution(median=622.0, p25=618.0, p75=625.0),
            ),
        )
        assert result.spec == "frost-death-knight"
        assert len(result.builds) == 2

    def test_rejects_missing_required_fields(self):
        """缺少必需字段被拒绝"""
        from src.models import TopBuildsResponse

        with pytest.raises(ValidationError):
            TopBuildsResponse(
                spec="frost-death-knight",
                # 缺少 encounter_id, difficulty
            )  # type: ignore


# ============================================================
# EncountersResponse 模型测试
# ============================================================
class TestEncountersResponseModel:
    """EncountersResponse（get_encounters 返回值）模型"""

    def test_valid_encounters_response(self):
        """有效的 encounters 返回值通过验证"""
        from src.models import Encounter, EncountersResponse, Zone

        result = EncountersResponse(
            expansion="Midnight",
            zones=[
                Zone(
                    id=100,
                    name="The Voidspire",
                    encounters=[
                        Encounter(id=3001, name="Vorasius"),
                    ],
                )
            ],
        )
        assert len(result.zones) == 1
        assert result.expansion == "Midnight"

    def test_empty_zones_is_valid(self):
        """空 zones 列表也有效"""
        from src.models import EncountersResponse

        result = EncountersResponse(expansion="Midnight", zones=[])
        assert result.zones == []

    def test_serialization_matches_expected_shape(self):
        """序列化输出匹配预期 JSON 格式"""
        from src.models import Encounter, EncountersResponse, Zone

        result = EncountersResponse(
            expansion="Midnight",
            zones=[
                Zone(
                    id=100,
                    name="The Voidspire",
                    encounters=[
                        Encounter(id=3001, name="Vorasius"),
                        Encounter(id=3002, name="Darkweaver"),
                    ],
                )
            ],
        )
        data = result.model_dump()

        # 验证顶层结构
        assert "expansion" in data
        assert "zones" in data
        assert isinstance(data["zones"], list)
        # 验证嵌套结构
        zone = data["zones"][0]
        assert zone["id"] == 100
        assert zone["name"] == "The Voidspire"
        assert len(zone["encounters"]) == 2
        assert zone["encounters"][0] == {"id": 3001, "name": "Vorasius"}


# ============================================================
# RateLimitInfo 模型测试
# ============================================================
class TestRateLimitInfoModel:
    """RateLimitInfo 速率限制模型"""

    def test_create_from_camel_case(self):
        """支持从 camelCase 别名创建"""
        from src.models import RateLimitInfo

        info = RateLimitInfo(
            limitPerHour=3600,
            pointsSpentThisHour=120.5,
            pointsResetIn=1800,
        )
        assert info.limit_per_hour == 3600
        assert info.points_spent_this_hour == 120.5
        assert info.points_reset_in == 1800

    def test_points_remaining_property(self):
        """points_remaining 属性计算正确"""
        from src.models import RateLimitInfo

        info = RateLimitInfo(
            limitPerHour=3600,
            pointsSpentThisHour=120.5,
            pointsResetIn=1800,
        )
        assert info.points_remaining == 3600 - 120.5

    def test_create_from_snake_case(self):
        """支持从 snake_case 字段名创建"""
        from src.models import RateLimitInfo

        info = RateLimitInfo(
            limit_per_hour=3600,
            points_spent_this_hour=100,
            points_reset_in=900,
        )
        assert info.limit_per_hour == 3600
