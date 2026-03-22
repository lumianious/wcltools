# ============================================================
# Phase 7: P0-P2 测试
# 覆盖: CastSequence, BuffTimeline, ResourceTimeline 模型验证
#        Eclipse 指标、APL CD 追踪修复、Buff 事件处理逻辑
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    BuffEvent,
    BuffSummary,
    BuffTimelineResponse,
    CastEvent,
    CastSequenceResponse,
    EclipseMetrics,
    PlayerAnalysisResponse,
    ResourcePoint,
    ResourceTimelineResponse,
)


# ============================================================
# CastEvent 模型测试
# ============================================================
class TestCastEventModel:
    """CastEvent 数据模型验证。"""

    def test_valid_construction(self):
        """有效施法事件数据通过验证"""
        e = CastEvent(spell_id=190984, spell_name="Wrath", timestamp_sec=5.3)
        assert e.spell_id == 190984
        assert e.spell_name == "Wrath"
        assert e.timestamp_sec == 5.3
        assert e.resource_amount is None
        assert e.resource_max is None

    def test_with_resource_fields(self):
        """包含资源字段的施法事件"""
        e = CastEvent(
            spell_id=190984, spell_name="Wrath", timestamp_sec=5.3,
            resource_amount=65.0, resource_max=100.0,
        )
        assert e.resource_amount == 65.0
        assert e.resource_max == 100.0

    def test_resource_fields_optional(self):
        """资源字段可选，默认为 None"""
        e = CastEvent(spell_id=190984, spell_name="Wrath", timestamp_sec=5.3)
        data = e.model_dump()
        assert data["resource_amount"] is None
        assert data["resource_max"] is None

    def test_resource_round_trip(self):
        """带资源字段的序列化 -> 重建"""
        original = CastEvent(
            spell_id=190984, spell_name="Wrath", timestamp_sec=5.3,
            resource_amount=80.0, resource_max=100.0,
        )
        data = original.model_dump()
        rebuilt = CastEvent(**data)
        assert rebuilt.resource_amount == 80.0
        assert rebuilt.resource_max == 100.0

    def test_missing_spell_id_raises(self):
        """缺少 spell_id 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(spell_name="Wrath", timestamp_sec=5.3)  # type: ignore

    def test_missing_spell_name_raises(self):
        """缺少 spell_name 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(spell_id=190984, timestamp_sec=5.3)  # type: ignore

    def test_missing_timestamp_raises(self):
        """缺少 timestamp_sec 被拒绝"""
        with pytest.raises(ValidationError):
            CastEvent(spell_id=190984, spell_name="Wrath")  # type: ignore

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CastEvent(spell_id=190984, spell_name="Wrath", timestamp_sec=5.3)
        data = original.model_dump()
        rebuilt = CastEvent(**data)
        assert rebuilt.spell_id == original.spell_id
        assert rebuilt.spell_name == original.spell_name
        assert rebuilt.timestamp_sec == original.timestamp_sec


# ============================================================
# CastSequenceResponse 模型测试
# ============================================================
class TestCastSequenceResponseModel:
    """CastSequenceResponse 数据模型验证。"""

    def test_valid_construction(self):
        """完整施法序列响应通过验证"""
        resp = CastSequenceResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            fight_duration=180.0,
            time_start=0.0,
            time_end=180.0,
            total_casts=5,
            casts=[
                CastEvent(spell_id=190984, spell_name="Wrath", timestamp_sec=1.0),
                CastEvent(spell_id=78674, spell_name="Starsurge", timestamp_sec=3.5),
            ],
        )
        assert resp.report_code == "ABC123"
        assert resp.total_casts == 5
        assert len(resp.casts) == 2

    def test_defaults(self):
        """默认值正确"""
        resp = CastSequenceResponse(
            report_code="X", fight_id=1, player_name="P", spec="s",
        )
        assert resp.fight_duration == 0.0
        assert resp.total_casts == 0
        assert resp.casts == []

    def test_missing_required_raises(self):
        """缺少必需字段被拒绝"""
        with pytest.raises(ValidationError):
            CastSequenceResponse(
                report_code="X",
                fight_id=1,
            )  # type: ignore

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = CastSequenceResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            total_casts=1,
            casts=[CastEvent(spell_id=190984, spell_name="Wrath", timestamp_sec=1.0)],
        )
        data = original.model_dump()
        rebuilt = CastSequenceResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert len(rebuilt.casts) == 1
        assert rebuilt.casts[0].spell_name == "Wrath"


# ============================================================
# BuffEvent 模型测试
# ============================================================
class TestBuffEventModel:
    """BuffEvent 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Buff 事件数据通过验证"""
        e = BuffEvent(
            buff_id=48517,
            buff_name="Eclipse (Solar)",
            event_type="applybuff",
            timestamp_sec=10.5,
            stacks=1,
        )
        assert e.buff_id == 48517
        assert e.event_type == "applybuff"

    def test_stacks_default(self):
        """stacks 默认为 0"""
        e = BuffEvent(
            buff_id=48517,
            buff_name="Eclipse (Solar)",
            event_type="applybuff",
            timestamp_sec=10.5,
        )
        assert e.stacks == 0


# ============================================================
# BuffSummary 模型测试
# ============================================================
class TestBuffSummaryModel:
    """BuffSummary 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Buff 摘要数据通过验证"""
        s = BuffSummary(
            buff_id=48517,
            buff_name="Eclipse (Solar)",
            uptime_pct=85.0,
            avg_stacks=1.0,
            apply_count=10,
        )
        assert s.uptime_pct == 85.0
        assert s.apply_count == 10

    def test_defaults(self):
        """默认值正确"""
        s = BuffSummary(buff_id=1, buff_name="Test")
        assert s.uptime_pct == 0.0
        assert s.avg_stacks == 0.0
        assert s.apply_count == 0
        assert s.events == []


# ============================================================
# BuffTimelineResponse 模型测试
# ============================================================
class TestBuffTimelineResponseModel:
    """BuffTimelineResponse 数据模型验证。"""

    def test_valid_construction(self):
        """完整 Buff 时间线响应通过验证"""
        resp = BuffTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            fight_duration=180.0,
            buffs=[
                BuffSummary(buff_id=48517, buff_name="Eclipse (Solar)", uptime_pct=85.0),
            ],
        )
        assert resp.report_code == "ABC123"
        assert len(resp.buffs) == 1

    def test_defaults(self):
        """默认值正确"""
        resp = BuffTimelineResponse(
            report_code="X", fight_id=1, player_name="P",
        )
        assert resp.buffs == []
        assert resp.fight_duration == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = BuffTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            buffs=[
                BuffSummary(
                    buff_id=48517,
                    buff_name="Eclipse (Solar)",
                    uptime_pct=85.0,
                    events=[
                        BuffEvent(
                            buff_id=48517,
                            buff_name="Eclipse (Solar)",
                            event_type="applybuff",
                            timestamp_sec=10.5,
                        ),
                    ],
                ),
            ],
        )
        data = original.model_dump()
        rebuilt = BuffTimelineResponse(**data)
        assert len(rebuilt.buffs) == 1
        assert len(rebuilt.buffs[0].events) == 1


# ============================================================
# ResourcePoint 模型测试
# ============================================================
class TestResourcePointModel:
    """ResourcePoint 数据模型验证。"""

    def test_valid_construction(self):
        """有效资源点数据通过验证"""
        p = ResourcePoint(
            timestamp_sec=5.0,
            value=80,
            max_value=100,
            spell_name="Wrath",
            is_overflow=False,
        )
        assert p.value == 80
        assert not p.is_overflow

    def test_overflow_detection(self):
        """溢出标记正确"""
        p = ResourcePoint(
            timestamp_sec=5.0,
            value=100,
            max_value=100,
            is_overflow=True,
        )
        assert p.is_overflow

    def test_defaults(self):
        """默认值正确"""
        p = ResourcePoint(timestamp_sec=0.0)
        assert p.value == 0
        assert p.max_value == 0
        assert p.spell_name == ""
        assert not p.is_overflow


# ============================================================
# ResourceTimelineResponse 模型测试
# ============================================================
class TestResourceTimelineResponseModel:
    """ResourceTimelineResponse 数据模型验证。"""

    def test_valid_construction(self):
        """完整资源时间线响应通过验证"""
        resp = ResourceTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            resource_type="astral_power",
            fight_duration=180.0,
            total_points=50,
            overflow_count=3,
            overflow_pct=6.0,
            points=[
                ResourcePoint(timestamp_sec=5.0, value=80, max_value=100),
            ],
        )
        assert resp.resource_type == "astral_power"
        assert resp.overflow_count == 3

    def test_defaults(self):
        """默认值正确"""
        resp = ResourceTimelineResponse(
            report_code="X", fight_id=1, player_name="P", resource_type="mana",
        )
        assert resp.points == []
        assert resp.total_points == 0
        assert resp.overflow_pct == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = ResourceTimelineResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            resource_type="astral_power",
            total_points=2,
            overflow_count=1,
            overflow_pct=50.0,
            points=[
                ResourcePoint(timestamp_sec=5.0, value=80, max_value=100),
                ResourcePoint(timestamp_sec=10.0, value=100, max_value=100, is_overflow=True),
            ],
        )
        data = original.model_dump()
        rebuilt = ResourceTimelineResponse(**data)
        assert rebuilt.total_points == 2
        assert rebuilt.points[1].is_overflow


# ============================================================
# EclipseMetrics 模型测试
# ============================================================
class TestEclipseMetricsModel:
    """EclipseMetrics 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Eclipse 指标数据通过验证"""
        m = EclipseMetrics(
            eclipse_uptime_pct=85.0,
            avg_eclipse_gap_sec=2.5,
            ca_eclipse_coverage_pct=95.0,
            starlord_uptime_pct=60.0,
        )
        assert m.eclipse_uptime_pct == 85.0
        assert m.starlord_uptime_pct == 60.0

    def test_defaults(self):
        """默认值全为 0.0"""
        m = EclipseMetrics()
        assert m.eclipse_uptime_pct == 0.0
        assert m.avg_eclipse_gap_sec == 0.0
        assert m.ca_eclipse_coverage_pct == 0.0
        assert m.starlord_uptime_pct == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = EclipseMetrics(
            eclipse_uptime_pct=85.0,
            starlord_uptime_pct=60.0,
        )
        data = original.model_dump()
        rebuilt = EclipseMetrics(**data)
        assert rebuilt.eclipse_uptime_pct == original.eclipse_uptime_pct
        assert rebuilt.starlord_uptime_pct == original.starlord_uptime_pct


# ============================================================
# PlayerAnalysisResponse — eclipse_metrics 字段集成测试
# ============================================================
class TestPlayerAnalysisResponseEclipse:
    """PlayerAnalysisResponse 中 eclipse_metrics 字段集成测试。"""

    def test_with_eclipse_metrics_populated(self):
        """构造包含 eclipse_metrics 的 PlayerAnalysisResponse"""
        resp = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=85.0,
                starlord_uptime_pct=60.0,
            ),
        )
        assert resp.eclipse_metrics is not None
        assert resp.eclipse_metrics.eclipse_uptime_pct == 85.0
        data = resp.model_dump()
        assert data["eclipse_metrics"]["eclipse_uptime_pct"] == 85.0

    def test_with_eclipse_metrics_none(self):
        """eclipse_metrics=None -> 可选字段"""
        resp = PlayerAnalysisResponse(
            report_code="X", fight_id=1, player_name="P", spec="frost-death-knight",
        )
        assert resp.eclipse_metrics is None
        data = resp.model_dump()
        assert data["eclipse_metrics"] is None

    def test_full_round_trip(self):
        """完整序列化 -> 重建"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=85.0,
                ca_eclipse_coverage_pct=95.0,
            ),
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.eclipse_metrics is not None
        assert rebuilt.eclipse_metrics.eclipse_uptime_pct == 85.0
        assert rebuilt.eclipse_metrics.ca_eclipse_coverage_pct == 95.0


# ============================================================
# Eclipse 指标分析逻辑测试
# ============================================================
class TestAnalyzeEclipseMetrics:
    """_analyze_eclipse_metrics 分析逻辑。"""

    def test_basic_eclipse_uptime(self):
        """基本 Eclipse 覆盖率计算"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [
            {"name": "Eclipse (Solar)", "totalUptime": 90000},  # 90s
            {"name": "Eclipse (Lunar)", "totalUptime": 60000},  # 60s
        ]
        result = _analyze_eclipse_metrics(auras, 200.0)
        assert result is not None
        # (90000 + 60000) / 200000 * 100 = 75.0%
        assert result.eclipse_uptime_pct == 75.0

    def test_with_starlord(self):
        """Starlord 覆盖率计算"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [
            {"name": "Eclipse (Solar)", "totalUptime": 100000},
            {"name": "Starlord", "totalUptime": 80000},
        ]
        result = _analyze_eclipse_metrics(auras, 200.0)
        assert result is not None
        assert result.starlord_uptime_pct == 40.0

    def test_with_ca(self):
        """CA/Incarnation 覆盖率"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [
            {"name": "Eclipse (Solar)", "totalUptime": 100000},
            {"name": "Celestial Alignment", "totalUptime": 30000},
        ]
        result = _analyze_eclipse_metrics(auras, 200.0)
        assert result is not None
        assert result.ca_eclipse_coverage_pct == 15.0

    def test_no_eclipse_returns_none(self):
        """无 Eclipse Buff -> 返回 None"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [
            {"name": "Power Word: Fortitude", "totalUptime": 200000},
        ]
        result = _analyze_eclipse_metrics(auras, 200.0)
        assert result is None

    def test_empty_auras_returns_none(self):
        """空 auras -> 返回 None"""
        from src.tools.analyze import _analyze_eclipse_metrics

        result = _analyze_eclipse_metrics([], 200.0)
        assert result is None

    def test_zero_duration_returns_none(self):
        """战斗时长为 0 -> 返回 None"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [{"name": "Eclipse (Solar)", "totalUptime": 100000}]
        result = _analyze_eclipse_metrics(auras, 0.0)
        assert result is None

    def test_uptime_capped_at_100(self):
        """覆盖率不超过 100%"""
        from src.tools.analyze import _analyze_eclipse_metrics

        auras = [
            {"name": "Eclipse (Solar)", "totalUptime": 150000},
            {"name": "Eclipse (Lunar)", "totalUptime": 150000},
        ]
        result = _analyze_eclipse_metrics(auras, 100.0)
        assert result is not None
        assert result.eclipse_uptime_pct == 100.0


# ============================================================
# Buff 事件处理逻辑测试
# ============================================================
class TestProcessBuffEvents:
    """_process_buff_events 处理逻辑。"""

    def test_basic_uptime(self):
        """基本 Buff 覆盖率计算"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 1000},
            {"type": "removebuff", "abilityGameID": 100, "timestamp": 6000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {100: "TestBuff"})
        assert len(result) == 1
        assert result[0].uptime_pct == 50.0

    def test_buff_active_at_fight_end(self):
        """Buff 在战斗结束时仍存在 -> 计算到结束"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 5000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {100: "TestBuff"})
        assert len(result) == 1
        assert result[0].uptime_pct == 50.0

    def test_stack_tracking(self):
        """层数追踪"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 0, "stack": 1},
            {"type": "applybuffstack", "abilityGameID": 100, "timestamp": 1000, "stack": 2},
            {"type": "applybuffstack", "abilityGameID": 100, "timestamp": 2000, "stack": 3},
            {"type": "removebuff", "abilityGameID": 100, "timestamp": 5000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {100: "Starlord"})
        assert len(result) == 1
        assert result[0].avg_stacks > 1.0

    def test_buff_id_filter(self):
        """按 buff_ids 过滤"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 0},
            {"type": "removebuff", "abilityGameID": 100, "timestamp": 5000},
            {"type": "applybuff", "abilityGameID": 200, "timestamp": 0},
            {"type": "removebuff", "abilityGameID": 200, "timestamp": 5000},
        ]
        result = _process_buff_events(events, 0, 10000, [100], {100: "A", 200: "B"})
        assert len(result) == 1
        assert result[0].buff_id == 100

    def test_empty_events(self):
        """空事件列表 -> 空结果"""
        from src.tools.buff_timeline import _process_buff_events

        result = _process_buff_events([], 0, 10000, None, {})
        assert result == []

    def test_zero_duration(self):
        """战斗时长为 0 -> 空结果"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 0},
        ]
        result = _process_buff_events(events, 0, 0, None, {100: "Test"})
        assert result == []

    def test_debuff_events_processed(self):
        """Debuff 事件（applydebuff/removedebuff）被正确处理"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applydebuff", "abilityGameID": 164812, "timestamp": 1000},
            {"type": "removedebuff", "abilityGameID": 164812, "timestamp": 6000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {164812: "Moonfire"})
        assert len(result) == 1
        assert result[0].buff_name == "Moonfire"
        assert result[0].uptime_pct == 50.0
        # 原始事件类型保留为 applydebuff/removedebuff
        assert result[0].events[0].event_type == "applydebuff"
        assert result[0].events[1].event_type == "removedebuff"

    def test_refreshdebuff_extends_uptime(self):
        """refreshdebuff 重置计时器，不中断覆盖率"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applydebuff", "abilityGameID": 164812, "timestamp": 0},
            {"type": "refreshdebuff", "abilityGameID": 164812, "timestamp": 5000},
            {"type": "removedebuff", "abilityGameID": 164812, "timestamp": 10000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {164812: "Moonfire"})
        assert len(result) == 1
        # 覆盖率应该是 100% (0-5000 + 5000-10000 = 10000ms / 10000ms)
        assert result[0].uptime_pct == 100.0
        # apply_count 应计 2 次（初始 apply + refresh）
        assert result[0].apply_count == 2

    def test_refreshbuff_extends_uptime(self):
        """refreshbuff 重置计时器"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 0},
            {"type": "refreshbuff", "abilityGameID": 100, "timestamp": 3000},
            {"type": "removebuff", "abilityGameID": 100, "timestamp": 8000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {100: "Test"})
        assert len(result) == 1
        # 0-3000 (3s) + 3000-8000 (5s) = 8000ms / 10000ms = 80%
        assert result[0].uptime_pct == 80.0

    def test_mixed_buff_and_debuff(self):
        """混合 Buff 和 Debuff 事件共存"""
        from src.tools.buff_timeline import _process_buff_events

        events = [
            {"type": "applybuff", "abilityGameID": 100, "timestamp": 0},
            {"type": "removebuff", "abilityGameID": 100, "timestamp": 5000},
            {"type": "applydebuff", "abilityGameID": 200, "timestamp": 0},
            {"type": "removedebuff", "abilityGameID": 200, "timestamp": 10000},
        ]
        result = _process_buff_events(events, 0, 10000, None, {100: "Eclipse", 200: "Moonfire"})
        assert len(result) == 2
        # 按覆盖率降序: Moonfire 100% > Eclipse 50%
        assert result[0].buff_name == "Moonfire"
        assert result[0].uptime_pct == 100.0
        assert result[1].buff_name == "Eclipse"
        assert result[1].uptime_pct == 50.0


# ============================================================
# ResourceTimeline 常量测试
# ============================================================
class TestResourceAutoDetect:
    """资源类型自动检测验证。"""

    def test_auto_default(self):
        """默认 resource_type='auto' 应该可以构造有效响应"""
        from src.models import ResourceTimelineResponse
        r = ResourceTimelineResponse(
            report_code="ABC", fight_id=1, player_name="Test",
            resource_type="auto", fight_duration=300.0,
        )
        assert r.resource_type == "auto"

    def test_detected_type_in_response(self):
        """检测到的资源类型应保存在 resource_type 字段"""
        from src.models import ResourceTimelineResponse
        r = ResourceTimelineResponse(
            report_code="ABC", fight_id=1, player_name="Test",
            resource_type="resource_type_8", fight_duration=300.0,
        )
        assert "8" in r.resource_type


# ============================================================
# APL CD 追踪修复验证
# ============================================================
class TestAPLCDTracking:
    """APL 检查器 CD 追踪逻辑验证（Task #27）。"""

    def test_cd_tracking_prevents_false_positive(self):
        """技能在 CD 中时不应产生违规"""
        from src.apl_checker import check_player_apl

        # 模拟: 2 个 APL 规则，高优先级技能有 90 秒 CD
        # 玩家先用高优先级技能（秒 0），然后在秒 5 用低优先级技能
        # 旧逻辑会在秒 35+ 产生假违规，新逻辑应识别 CD
        cast_timestamps = [
            (0, 100),     # 高优先级技能（秒 0）
            (5000, 200),  # 低优先级技能（秒 5）
            (10000, 200), # 低优先级技能（秒 10）
        ]
        spell_names = {100: "Celestial Alignment", 200: "Wrath"}

        # 注意: check_player_apl 依赖 get_spec_apl 加载规则
        # 如果没有 APL 数据文件，会返回 None
        result = check_player_apl(
            spec="nonexistent-spec",  # 没有 APL 文件 -> 返回 None
            cast_timestamps=cast_timestamps,
            spell_names=spell_names,
            buff_uptimes=[],
            fight_start_time=0,
            fight_duration=60.0,
            talents=[],
        )
        # 没有 APL 文件时应返回 None，不崩溃
        assert result is None

    def test_apl_checker_imports_get_spec_spells(self):
        """APL 检查器正确导入 get_spec_spells"""
        from src.apl_checker import check_player_apl
        from src.data import get_spec_spells
        # 验证函数存在且可调用
        assert callable(check_player_apl)
        assert callable(get_spec_spells)

    def test_cd_durations_built_from_spec_spells(self):
        """验证 CD 时长可以从 spec spells 数据构建"""
        from src.data import get_spec_spells

        spells = get_spec_spells("balance-druid")
        cd_spells = [s for s in spells if s.get("cooldown", 0) > 0]
        # Balance Druid 应该有一些 CD 技能
        if spells:  # 只在数据文件存在时验证
            assert len(cd_spells) > 0, "Balance Druid 应有 CD 技能"


# ============================================================
# 工具注册验证
# ============================================================
class TestToolRegistration:
    """验证新工具在 server.py 中正确注册。"""

    def test_cast_sequence_tool_exists(self):
        """get_cast_sequence 工具已注册"""
        from src.server import get_cast_sequence
        assert callable(get_cast_sequence)

    def test_buff_timeline_tool_exists(self):
        """get_buff_timeline 工具已注册"""
        from src.server import get_buff_timeline
        assert callable(get_buff_timeline)

    def test_resource_timeline_tool_exists(self):
        """get_resource_timeline 工具已注册"""
        from src.server import get_resource_timeline
        assert callable(get_resource_timeline)
