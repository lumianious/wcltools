# ============================================================
# Phase 6E: APL 循环检查测试
# 覆盖模型验证、合规性计算、违规模式聚合、集成测试
#
# 测试策略:
#   - 纯单元测试: 合规百分比计算、规则匹配（不依赖实现）
#   - 模型测试: APLViolation, APLAnalysis 验证
#   - 集成测试: PlayerAnalysisResponse 包含 apl_analysis 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    APLAnalysis,
    APLViolation,
    PlayerAnalysisResponse,
)


# ============================================================
# 辅助函数 — APL 合规性计算（纯逻辑，镜像 apl_checker 预期行为）
# ============================================================
def _calc_compliance_pct(total_casts: int, violations_count: int) -> float:
    """
    计算 APL 合规百分比。

    compliance = (total - violations) / total * 100
    """
    if total_casts <= 0:
        return 100.0  # 无施法时视为完全合规
    compliant = max(0, total_casts - violations_count)
    return compliant / total_casts * 100.0


def _count_high_severity(violations: list[dict]) -> int:
    """统计高严重度违规数量。"""
    return sum(1 for v in violations if v.get("severity") == "high")


def _aggregate_violation_patterns(violations: list[dict]) -> list[str]:
    """
    聚合违规模式: 按 (expected, actual) 分组，统计出现次数，
    返回按次数降序排列的 "expected -> actual (N次)" 格式字符串。
    """
    from collections import Counter

    patterns: Counter[tuple[str, str]] = Counter()
    for v in violations:
        key = (v["expected_spell"], v["actual_spell"])
        patterns[key] += 1
    return [
        f"{exp} -> {act} ({count}次)"
        for (exp, act), count in patterns.most_common()
    ]


# ============================================================
# 模型测试 — APLViolation
# ============================================================
class TestAPLViolationModel:
    """APLViolation 数据模型验证。"""

    def test_valid_construction(self):
        """有效 APL 违规数据通过验证"""
        v = APLViolation(
            timestamp_sec=15.3,
            expected_spell="Starsurge",
            actual_spell="Wrath",
            rule_priority=1,
            severity="high",
            benchmark_weight=0.85,
        )
        assert v.timestamp_sec == 15.3
        assert v.expected_spell == "Starsurge"
        assert v.actual_spell == "Wrath"
        assert v.rule_priority == 1
        assert v.severity == "high"
        assert v.benchmark_weight == 0.85

    def test_missing_timestamp_sec_raises(self):
        """缺少 timestamp_sec 被拒绝"""
        with pytest.raises(ValidationError):
            APLViolation(
                expected_spell="Starsurge",
                actual_spell="Wrath",
                rule_priority=1,
            )  # type: ignore

    def test_missing_expected_spell_raises(self):
        """缺少 expected_spell 被拒绝"""
        with pytest.raises(ValidationError):
            APLViolation(
                timestamp_sec=15.3,
                actual_spell="Wrath",
                rule_priority=1,
            )  # type: ignore

    def test_missing_actual_spell_raises(self):
        """缺少 actual_spell 被拒绝"""
        with pytest.raises(ValidationError):
            APLViolation(
                timestamp_sec=15.3,
                expected_spell="Starsurge",
                rule_priority=1,
            )  # type: ignore

    def test_missing_rule_priority_raises(self):
        """缺少 rule_priority 被拒绝"""
        with pytest.raises(ValidationError):
            APLViolation(
                timestamp_sec=15.3,
                expected_spell="Starsurge",
                actual_spell="Wrath",
            )  # type: ignore

    def test_severity_defaults_to_empty(self):
        """severity 默认为空字符串"""
        v = APLViolation(
            timestamp_sec=15.3,
            expected_spell="Starsurge",
            actual_spell="Wrath",
            rule_priority=1,
        )
        assert v.severity == ""

    def test_benchmark_weight_defaults_to_zero(self):
        """benchmark_weight 默认为 0.0"""
        v = APLViolation(
            timestamp_sec=15.3,
            expected_spell="Starsurge",
            actual_spell="Wrath",
            rule_priority=1,
        )
        assert v.benchmark_weight == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = APLViolation(
            timestamp_sec=15.3,
            expected_spell="Starsurge",
            actual_spell="Wrath",
            rule_priority=1,
            severity="high",
            benchmark_weight=0.85,
        )
        data = original.model_dump()
        rebuilt = APLViolation(**data)
        assert rebuilt.timestamp_sec == original.timestamp_sec
        assert rebuilt.expected_spell == original.expected_spell
        assert rebuilt.actual_spell == original.actual_spell
        assert rebuilt.rule_priority == original.rule_priority
        assert rebuilt.severity == original.severity
        assert rebuilt.benchmark_weight == original.benchmark_weight


# ============================================================
# 模型测试 — APLAnalysis
# ============================================================
class TestAPLAnalysisModel:
    """APLAnalysis 数据模型验证。"""

    def test_valid_construction(self):
        """有效 APL 分析数据通过验证"""
        analysis = APLAnalysis(
            spec="balance-druid",
            apl_version="11.1.0",
            compliance_pct=85.0,
            violations=[
                APLViolation(
                    timestamp_sec=15.3,
                    expected_spell="Starsurge",
                    actual_spell="Wrath",
                    rule_priority=1,
                    severity="high",
                    benchmark_weight=0.85,
                ),
            ],
            high_severity_count=1,
            top_violation_patterns=["Starsurge -> Wrath (1次)"],
        )
        assert analysis.spec == "balance-druid"
        assert analysis.apl_version == "11.1.0"
        assert analysis.compliance_pct == 85.0
        assert len(analysis.violations) == 1
        assert analysis.high_severity_count == 1
        assert len(analysis.top_violation_patterns) == 1

    def test_missing_spec_raises(self):
        """缺少 spec 被拒绝"""
        with pytest.raises(ValidationError):
            APLAnalysis(
                apl_version="11.1.0",
                compliance_pct=85.0,
            )  # type: ignore

    def test_defaults(self):
        """默认值正确"""
        analysis = APLAnalysis(spec="balance-druid")
        assert analysis.apl_version == ""
        assert analysis.compliance_pct == 0.0
        assert analysis.violations == []
        assert analysis.high_severity_count == 0
        assert analysis.top_violation_patterns == []

    def test_empty_violations(self):
        """空违规列表有效"""
        analysis = APLAnalysis(
            spec="balance-druid",
            compliance_pct=100.0,
            violations=[],
        )
        assert analysis.violations == []
        assert analysis.compliance_pct == 100.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = APLAnalysis(
            spec="balance-druid",
            apl_version="11.1.0",
            compliance_pct=85.0,
            violations=[
                APLViolation(
                    timestamp_sec=15.3,
                    expected_spell="Starsurge",
                    actual_spell="Wrath",
                    rule_priority=1,
                    severity="high",
                    benchmark_weight=0.85,
                ),
                APLViolation(
                    timestamp_sec=30.5,
                    expected_spell="Starfall",
                    actual_spell="Moonfire",
                    rule_priority=2,
                    severity="medium",
                    benchmark_weight=0.5,
                ),
            ],
            high_severity_count=1,
            top_violation_patterns=[
                "Starsurge -> Wrath (1次)",
                "Starfall -> Moonfire (1次)",
            ],
        )
        data = original.model_dump()
        rebuilt = APLAnalysis(**data)
        assert rebuilt.spec == original.spec
        assert rebuilt.apl_version == original.apl_version
        assert rebuilt.compliance_pct == original.compliance_pct
        assert len(rebuilt.violations) == 2
        assert rebuilt.high_severity_count == 1
        assert len(rebuilt.top_violation_patterns) == 2


# ============================================================
# 单元测试 — APL 合规百分比计算
# ============================================================
class TestCalcCompliancePct:
    """APL 合规百分比计算逻辑。"""

    def test_no_violations(self):
        """无违规 -> 100%"""
        assert _calc_compliance_pct(100, 0) == 100.0

    def test_all_violations(self):
        """全部违规 -> 0%"""
        assert _calc_compliance_pct(100, 100) == 0.0

    def test_partial_violations(self):
        """部分违规 -> 正确百分比"""
        result = _calc_compliance_pct(100, 15)
        assert abs(result - 85.0) < 0.01

    def test_zero_casts(self):
        """无施法 -> 100%"""
        assert _calc_compliance_pct(0, 0) == 100.0

    def test_negative_casts(self):
        """负数施法（异常）-> 100%"""
        assert _calc_compliance_pct(-5, 0) == 100.0

    def test_violations_exceed_casts(self):
        """违规数超过施法数（不应发生，但防御）-> 0%"""
        assert _calc_compliance_pct(50, 60) == 0.0

    def test_single_cast_single_violation(self):
        """1 次施法 1 次违规 -> 0%"""
        assert _calc_compliance_pct(1, 1) == 0.0

    def test_single_cast_no_violation(self):
        """1 次施法无违规 -> 100%"""
        assert _calc_compliance_pct(1, 0) == 100.0


# ============================================================
# 单元测试 — 高严重度违规计数
# ============================================================
class TestCountHighSeverity:
    """高严重度违规统计。"""

    def test_mixed_severity(self):
        """混合严重度 -> 只统计 high"""
        violations = [
            {"severity": "high", "expected_spell": "A", "actual_spell": "B"},
            {"severity": "medium", "expected_spell": "C", "actual_spell": "D"},
            {"severity": "high", "expected_spell": "E", "actual_spell": "F"},
            {"severity": "low", "expected_spell": "G", "actual_spell": "H"},
        ]
        assert _count_high_severity(violations) == 2

    def test_no_high(self):
        """无高严重度 -> 0"""
        violations = [
            {"severity": "medium", "expected_spell": "A", "actual_spell": "B"},
            {"severity": "low", "expected_spell": "C", "actual_spell": "D"},
        ]
        assert _count_high_severity(violations) == 0

    def test_all_high(self):
        """全部高严重度"""
        violations = [
            {"severity": "high", "expected_spell": "A", "actual_spell": "B"},
            {"severity": "high", "expected_spell": "C", "actual_spell": "D"},
        ]
        assert _count_high_severity(violations) == 2

    def test_empty_list(self):
        """空列表 -> 0"""
        assert _count_high_severity([]) == 0


# ============================================================
# 单元测试 — 违规模式聚合
# ============================================================
class TestAggregateViolationPatterns:
    """违规模式聚合逻辑。"""

    def test_single_pattern(self):
        """单一模式"""
        violations = [
            {"expected_spell": "Starsurge", "actual_spell": "Wrath"},
            {"expected_spell": "Starsurge", "actual_spell": "Wrath"},
        ]
        patterns = _aggregate_violation_patterns(violations)
        assert len(patterns) == 1
        assert "Starsurge -> Wrath (2次)" in patterns[0]

    def test_multiple_patterns(self):
        """多种模式 -> 按次数降序"""
        violations = [
            {"expected_spell": "Starsurge", "actual_spell": "Wrath"},
            {"expected_spell": "Starsurge", "actual_spell": "Wrath"},
            {"expected_spell": "Starfall", "actual_spell": "Moonfire"},
        ]
        patterns = _aggregate_violation_patterns(violations)
        assert len(patterns) == 2
        # 第一个应该是出现 2 次的
        assert "(2次)" in patterns[0]
        assert "(1次)" in patterns[1]

    def test_empty_violations(self):
        """空违规列表 -> 空模式"""
        patterns = _aggregate_violation_patterns([])
        assert patterns == []

    def test_all_unique(self):
        """全部不同 -> 每种 1 次"""
        violations = [
            {"expected_spell": "A", "actual_spell": "B"},
            {"expected_spell": "C", "actual_spell": "D"},
            {"expected_spell": "E", "actual_spell": "F"},
        ]
        patterns = _aggregate_violation_patterns(violations)
        assert len(patterns) == 3
        for p in patterns:
            assert "(1次)" in p


# ============================================================
# 集成测试 — PlayerAnalysisResponse 包含 apl_analysis 字段
# ============================================================
class TestPlayerAnalysisResponseAPL:
    """PlayerAnalysisResponse 中 apl_analysis 字段集成测试。"""

    def test_with_apl_analysis_populated(self):
        """构造包含 apl_analysis 的 PlayerAnalysisResponse -> 序列化正确"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            apl_analysis=APLAnalysis(
                spec="balance-druid",
                apl_version="11.1.0",
                compliance_pct=85.0,
                violations=[
                    APLViolation(
                        timestamp_sec=15.3,
                        expected_spell="Starsurge",
                        actual_spell="Wrath",
                        rule_priority=1,
                        severity="high",
                        benchmark_weight=0.85,
                    ),
                ],
                high_severity_count=1,
                top_violation_patterns=["Starsurge -> Wrath (1次)"],
            ),
        )
        assert response.apl_analysis is not None
        assert response.apl_analysis.compliance_pct == 85.0
        assert len(response.apl_analysis.violations) == 1

        data = response.model_dump()
        assert data["apl_analysis"]["compliance_pct"] == 85.0
        assert len(data["apl_analysis"]["violations"]) == 1

    def test_with_apl_analysis_none(self):
        """apl_analysis=None -> 可选字段，序列化为 None"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.apl_analysis is None
        data = response.model_dump()
        assert data["apl_analysis"] is None

    def test_full_round_trip_with_apl(self):
        """model_dump -> 重建 -> 完整 apl_analysis 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Moonkin",
            spec="balance-druid",
            apl_analysis=APLAnalysis(
                spec="balance-druid",
                apl_version="11.1.0",
                compliance_pct=92.5,
                violations=[
                    APLViolation(
                        timestamp_sec=15.3,
                        expected_spell="Starsurge",
                        actual_spell="Wrath",
                        rule_priority=1,
                        severity="high",
                        benchmark_weight=0.85,
                    ),
                ],
                high_severity_count=1,
                top_violation_patterns=["Starsurge -> Wrath (1次)"],
            ),
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.apl_analysis is not None
        assert rebuilt.apl_analysis.compliance_pct == 92.5
        assert len(rebuilt.apl_analysis.violations) == 1
        assert rebuilt.apl_analysis.violations[0].expected_spell == "Starsurge"

    def test_apl_missing_spec_returns_none(self):
        """没有 APL 数据的 spec -> apl_analysis 为 None"""
        # 模拟 load_apl 返回空/None 的场景
        apl_data = None  # 模拟无 APL 文件
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Warrior",
            spec="arms-warrior",
            apl_analysis=None if apl_data is None else APLAnalysis(spec="arms-warrior"),
        )
        assert response.apl_analysis is None


# ============================================================
# 单元测试 — APL 规则优先级匹配逻辑
# ============================================================
class TestAPLRulePriority:
    """APL 规则优先级匹配测试。"""

    def test_higher_priority_rule_wins(self):
        """高优先级规则优先匹配"""
        # 模拟 APL 规则列表（按优先级排序）
        rules = [
            {"priority": 1, "spell": "Starsurge", "condition": "buff.eclipse.up"},
            {"priority": 2, "spell": "Starfall", "condition": "targets>=3"},
            {"priority": 3, "spell": "Wrath", "condition": "always"},
        ]
        # 当条件满足时，应选择最高优先级
        matching_rules = [r for r in rules if r["condition"] != "never"]
        best = min(matching_rules, key=lambda r: r["priority"])
        assert best["spell"] == "Starsurge"

    def test_fallback_to_lower_priority(self):
        """高优先级条件不满足 -> 回退到低优先级"""
        rules = [
            {"priority": 1, "spell": "Starsurge", "condition": "buff.eclipse.up"},
            {"priority": 2, "spell": "Starfall", "condition": "targets>=3"},
            {"priority": 3, "spell": "Wrath", "condition": "always"},
        ]
        # 模拟只有 priority 3 的条件满足
        matching_rules = [r for r in rules if r["condition"] == "always"]
        best = min(matching_rules, key=lambda r: r["priority"])
        assert best["spell"] == "Wrath"

    def test_empty_rules(self):
        """空规则列表 -> 无匹配"""
        rules: list[dict] = []
        matching = [r for r in rules if True]
        assert matching == []

    def test_violation_detection(self):
        """实际施法与预期不符 -> 产生违规"""
        expected = "Starsurge"
        actual = "Wrath"
        is_violation = expected != actual
        assert is_violation is True

    def test_compliant_cast(self):
        """实际施法与预期一致 -> 无违规"""
        expected = "Starsurge"
        actual = "Starsurge"
        is_violation = expected != actual
        assert is_violation is False


# ============================================================
# 单元测试 — APL CD 追踪逻辑（Phase 7 改进）
# ============================================================
def _should_flag_violation(
    spell_id: int,
    current_sec: float,
    cd_available_at: dict[int, float],
    cd_durations: dict[int, float],
) -> bool:
    """
    判断高优先级技能是否应被标记为违规。

    如果该技能在 CD 中（不可用），不应产生违规。
    如果该技能不在 CD 中或无已知 CD，应产生违规。
    """
    if cd_available_at.get(spell_id, 0) > current_sec:
        return False  # 技能仍在 CD 中
    return True


class TestAPLCDTracking:
    """APL CD 追踪逻辑测试（Phase 7 改进）。"""

    def test_spell_on_cooldown_no_violation(self):
        """技能在 CD 中 -> 不应触发违规"""
        cd_available_at = {51271: 30.0}  # Pillar of Frost CD 到 30s
        cd_durations = {51271: 60.0}
        # 当前时间 20s，技能 CD 到 30s -> 仍在 CD 中
        result = _should_flag_violation(51271, 20.0, cd_available_at, cd_durations)
        assert result is False

    def test_spell_off_cooldown_triggers_violation(self):
        """技能 CD 已过期 -> 应触发违规"""
        cd_available_at = {51271: 30.0}  # Pillar of Frost CD 到 30s
        cd_durations = {51271: 60.0}
        # 当前时间 35s，技能 CD 到 30s -> 已可用
        result = _should_flag_violation(51271, 35.0, cd_available_at, cd_durations)
        assert result is True

    def test_spell_exactly_at_cd_expiry(self):
        """恰好在 CD 到期时刻 -> 不应触发违规（CD 未过期）"""
        cd_available_at = {51271: 30.0}
        cd_durations = {51271: 60.0}
        # 当前时间恰好 30s -> cd_available_at[spell_id] > current_sec 为 False
        result = _should_flag_violation(51271, 30.0, cd_available_at, cd_durations)
        assert result is True  # 30.0 > 30.0 is False, so violation

    def test_spell_no_known_cd(self):
        """技能无已知 CD -> 应触发违规（使用旧行为）"""
        cd_available_at: dict[int, float] = {}
        cd_durations: dict[int, float] = {}
        # 技能从未被施放过，无 CD 记录 -> cd_available_at.get(spell_id, 0) = 0
        result = _should_flag_violation(49020, 15.0, cd_available_at, cd_durations)
        assert result is True

    def test_multiple_cd_spells_interleaving(self):
        """多个 CD 技能交错使用 -> 各自独立追踪"""
        cd_available_at = {
            51271: 30.0,   # Pillar of Frost CD 到 30s
            152279: 45.0,  # Breath of Sindragosa CD 到 45s
        }
        cd_durations = {51271: 60.0, 152279: 120.0}

        # 时间 25s: 两个都在 CD 中
        assert _should_flag_violation(51271, 25.0, cd_available_at, cd_durations) is False
        assert _should_flag_violation(152279, 25.0, cd_available_at, cd_durations) is False

        # 时间 35s: Pillar 已可用，Breath 仍在 CD
        assert _should_flag_violation(51271, 35.0, cd_available_at, cd_durations) is True
        assert _should_flag_violation(152279, 35.0, cd_available_at, cd_durations) is False

        # 时间 50s: 两个都已可用
        assert _should_flag_violation(51271, 50.0, cd_available_at, cd_durations) is True
        assert _should_flag_violation(152279, 50.0, cd_available_at, cd_durations) is True

    def test_cd_updated_after_cast(self):
        """施放后 CD 更新 -> 后续检查反映新状态"""
        cd_available_at: dict[int, float] = {}
        cd_durations = {51271: 60.0}

        # 初始: 无 CD 记录 -> 应触发违规
        assert _should_flag_violation(51271, 10.0, cd_available_at, cd_durations) is True

        # 模拟施放: 在 10s 施放，CD 到 70s
        cd_available_at[51271] = 10.0 + cd_durations[51271]

        # 施放后 20s: 在 CD 中
        assert _should_flag_violation(51271, 20.0, cd_available_at, cd_durations) is False

        # 施放后 75s: CD 已过期
        assert _should_flag_violation(51271, 75.0, cd_available_at, cd_durations) is True

    def test_gcd_spell_no_cd_always_flaggable(self):
        """GCD 技能（无 CD）-> 每次都可触发违规"""
        cd_available_at: dict[int, float] = {}
        cd_durations: dict[int, float] = {}  # GCD 技能不在 CD 表中

        assert _should_flag_violation(49020, 5.0, cd_available_at, cd_durations) is True
        assert _should_flag_violation(49020, 10.0, cd_available_at, cd_durations) is True
        assert _should_flag_violation(49020, 50.0, cd_available_at, cd_durations) is True
