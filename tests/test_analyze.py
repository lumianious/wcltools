# ============================================================
# analyze_player_log 工具测试
# 覆盖 URL 解析、循环对比、CD 对比、天赋对比、问题优先级、集成流程
#
# 测试目标模块: src.tools.analyze (Phase 5)
# 数据模型:
#   SpellGap, CooldownIssue, DefensiveIssue,
#   BuildDivergence, PlayerAnalysisResponse
#
# 测试策略:
#   - 纯单元测试: URL 解析、差距计算、优先级排序（不依赖实现）
#   - 集成测试: 通过 mock WCL client + mock 基准工具测试完整流程
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models import (
    APLAnalysis,
    APLViolation,
    BuildDivergence,
    CDWindowThroughput,
    CooldownIssue,
    CooldownWindowDetail,
    DefensiveIssue,
    DowntimeAnalysis,
    DowntimeWindow,
    EclipseMetrics,
    EventLinkingAnalysis,
    PlayerAnalysisResponse,
    SpellGap,
    TalentUsageAnalysis,
    TalentUsageGap,
)
from tests.conftest import MockWCLClient


# ============================================================
# 辅助函数 — URL/报告代码解析（纯逻辑，镜像 analyze 模块预期行为）
# ============================================================
def _extract_report_code(url_or_code: str) -> str:
    """
    从 WCL URL 或纯代码中提取 report code。

    支持格式:
      - "ABC123"
      - "https://www.warcraftlogs.com/reports/ABC123"
      - "https://www.warcraftlogs.com/reports/ABC123#fight=3&source=5"
      - "https://www.warcraftlogs.com/reports/ABC123/"
    """
    s = url_or_code.strip()
    if "/" not in s:
        return s
    # 移除 fragment
    if "#" in s:
        s = s.split("#")[0]
    # 移除 trailing slash
    s = s.rstrip("/")
    # 取最后一段
    return s.rsplit("/", 1)[-1]


def _calc_percentile(
    player_casts: int,
    p25: float,
    p50: float,
    p75: float,
) -> str:
    """根据百分位阈值判定玩家表现区间。"""
    if player_casts < p25:
        return "below_p25"
    if player_casts < p50:
        return "p25_p50"
    if player_casts < p75:
        return "p50_p75"
    return "above_p75"


def _calc_verdict(percentile: str) -> str:
    """根据百分位区间判定施法行为。"""
    if percentile == "below_p25":
        return "undercast"
    if percentile in ("p50_p75", "above_p75"):
        return "ok"
    return "ok"  # p25_p50 也算 ok


def _calc_similarity(
    player_talents: set[str],
    meta_talents: set[str],
) -> float:
    """计算天赋重合率: 交集 / 并集 * 100。"""
    if not player_talents and not meta_talents:
        return 100.0
    if not player_talents or not meta_talents:
        return 0.0
    intersection = player_talents & meta_talents
    union = player_talents | meta_talents
    return len(intersection) / len(union) * 100.0


# ============================================================
# 单元测试 — URL 解析
# ============================================================
class TestUrlParsing:
    """Report URL/code 解析测试。"""

    def test_plain_code(self):
        """纯 code → 原样返回"""
        assert _extract_report_code("ABC123") == "ABC123"

    def test_full_url(self):
        """完整 URL → 提取 code"""
        url = "https://www.warcraftlogs.com/reports/ABC123"
        assert _extract_report_code(url) == "ABC123"

    def test_url_with_fragment(self):
        """URL 含 fragment → 忽略 fragment"""
        url = "https://www.warcraftlogs.com/reports/ABC123#fight=3&source=5"
        assert _extract_report_code(url) == "ABC123"

    def test_url_with_trailing_slash(self):
        """URL 末尾有斜杠 → 正确提取"""
        url = "https://www.warcraftlogs.com/reports/ABC123/"
        assert _extract_report_code(url) == "ABC123"

    def test_whitespace_trimmed(self):
        """前后空白 → 自动去除"""
        assert _extract_report_code("  ABC123  ") == "ABC123"

    def test_url_with_both(self):
        """URL 含 trailing slash + fragment → 正确提取"""
        url = "https://www.warcraftlogs.com/reports/XYZ789/#fight=1"
        assert _extract_report_code(url) == "XYZ789"


# ============================================================
# 单元测试 — 循环（Rotation）对比
# ============================================================
class TestRotationComparison:
    """技能施法次数差距计算测试。"""

    def test_undercast_spell(self):
        """玩家 30 次，基准 p25=40 → below_p25, undercast"""
        percentile = _calc_percentile(30, p25=40, p50=50, p75=60)
        assert percentile == "below_p25"
        verdict = _calc_verdict(percentile)
        assert verdict == "undercast"

    def test_ok_spell(self):
        """玩家 50 次，基准 p50=45, p75=60 → p50_p75, ok"""
        percentile = _calc_percentile(50, p25=30, p50=45, p75=60)
        assert percentile == "p50_p75"
        verdict = _calc_verdict(percentile)
        assert verdict == "ok"

    def test_above_p75(self):
        """玩家 70 次，基准 p75=60 → above_p75, ok"""
        percentile = _calc_percentile(70, p25=30, p50=45, p75=60)
        assert percentile == "above_p75"
        verdict = _calc_verdict(percentile)
        assert verdict == "ok"

    def test_missing_spell(self):
        """玩家 0 次施法 → below_p25, undercast"""
        percentile = _calc_percentile(0, p25=10, p50=20, p75=30)
        assert percentile == "below_p25"
        verdict = _calc_verdict(percentile)
        assert verdict == "undercast"

    def test_spell_gap_model(self):
        """SpellGap 模型构造和字段验证"""
        gap = SpellGap(
            name="Obliterate",
            spell_id=49020,
            player_casts=30,
            player_cpm=5.0,
            benchmark_median=50.0,
            benchmark_cpm=8.3,
            percentile="below_p25",
            verdict="undercast",
        )
        assert gap.name == "Obliterate"
        assert gap.player_casts == 30
        assert gap.percentile == "below_p25"
        assert gap.verdict == "undercast"


# ============================================================
# 单元测试 — CD 对比
# ============================================================
class TestCooldownComparison:
    """冷却技能使用差距计算测试。"""

    def test_missed_uses(self):
        """玩家 3 次，基准中位 5 次 → 漏用 2 次"""
        missed = max(0, round(5 - 3))
        assert missed == 2

    def test_full_usage(self):
        """玩家 5 次，基准 5 次 → 漏用 0 次"""
        missed = max(0, round(5 - 5))
        assert missed == 0

    def test_over_usage(self):
        """玩家 6 次，基准 5 次 → 漏用 0 次（不可能负数）"""
        missed = max(0, round(5 - 6))
        assert missed == 0

    def test_cooldown_issue_model(self):
        """CooldownIssue 模型构造和字段验证"""
        issue = CooldownIssue(
            name="Pillar of Frost",
            spell_id=51271,
            player_casts=3,
            benchmark_median_casts=5.0,
            missed_uses=2,
        )
        assert issue.name == "Pillar of Frost"
        assert issue.missed_uses == 2

    def test_zero_benchmark(self):
        """基准 0 次（罕见技能）→ 漏用 0"""
        missed = max(0, round(0 - 0))
        assert missed == 0


# ============================================================
# 单元测试 — 天赋对比
# ============================================================
class TestBuildComparison:
    """天赋构建相似度计算测试。"""

    def test_perfect_match(self):
        """完全一致 → 100% 相似度"""
        player = {"TalentA", "TalentB", "TalentC"}
        meta = {"TalentA", "TalentB", "TalentC"}
        similarity = _calc_similarity(player, meta)
        assert similarity == 100.0

    def test_partial_match(self):
        """4/5 共同天赋 → 80% 相似度"""
        player = {"A", "B", "C", "D", "E"}
        meta = {"A", "B", "C", "D", "F"}
        similarity = _calc_similarity(player, meta)
        # 交集 4, 并集 6 → 66.7%
        assert abs(similarity - 66.67) < 1.0

    def test_no_match(self):
        """完全不同 → 0% 相似度"""
        player = {"A", "B", "C"}
        meta = {"D", "E", "F"}
        similarity = _calc_similarity(player, meta)
        assert similarity == 0.0

    def test_both_empty(self):
        """都为空 → 100% 相似度"""
        similarity = _calc_similarity(set(), set())
        assert similarity == 100.0

    def test_player_empty(self):
        """玩家天赋为空 → 0%"""
        similarity = _calc_similarity(set(), {"A", "B"})
        assert similarity == 0.0

    def test_divergence_lists(self):
        """对比生成正确的缺失/多余天赋列表"""
        player = {"A", "B", "C", "D"}
        meta = {"A", "B", "E", "F"}
        missing = sorted(meta - player)  # 玩家缺少的 meta 天赋
        extra = sorted(player - meta)    # 玩家多出的天赋
        assert missing == ["E", "F"]
        assert extra == ["C", "D"]

    def test_build_divergence_model(self):
        """BuildDivergence 模型构造"""
        div = BuildDivergence(
            best_match_build=1,
            similarity_pct=80.0,
            missing_meta_talents=["Obliteration"],
            extra_talents=["Breath of Sindragosa"],
        )
        assert div.best_match_build == 1
        assert div.similarity_pct == 80.0
        assert len(div.missing_meta_talents) == 1
        assert len(div.extra_talents) == 1


# ============================================================
# 单元测试 — 问题优先级排序
# ============================================================
class TestTopIssues:
    """问题优先级生成测试。"""

    def test_generates_issues(self):
        """给定差距数据，生成可读字符串"""
        gaps = [
            SpellGap(
                name="Obliterate", spell_id=49020,
                player_casts=20, player_cpm=3.3,
                benchmark_median=40, benchmark_cpm=6.7,
                percentile="below_p25", verdict="undercast",
            ),
        ]
        cd_issues = [
            CooldownIssue(
                name="Pillar of Frost", spell_id=51271,
                player_casts=3, benchmark_median_casts=5,
                missed_uses=2,
            ),
        ]
        # 模拟 top_issues 生成逻辑
        issues: list[str] = []
        for g in gaps:
            if g.verdict == "undercast":
                issues.append(
                    f"{g.name} 施法次数不足 "
                    f"({g.player_casts} vs 基准 {g.benchmark_median})"
                )
        for c in cd_issues:
            if c.missed_uses > 0:
                issues.append(
                    f"{c.name} 漏用 {c.missed_uses} 次"
                )

        assert len(issues) == 2
        assert "Obliterate" in issues[0]
        assert "Pillar of Frost" in issues[1]

    def test_empty_when_perfect(self):
        """无差距 → 无问题"""
        gaps: list[SpellGap] = []
        cd_issues: list[CooldownIssue] = []

        issues: list[str] = []
        for g in gaps:
            if g.verdict == "undercast":
                issues.append(f"{g.name} undercast")
        for c in cd_issues:
            if c.missed_uses > 0:
                issues.append(f"{c.name} missed")

        assert issues == []

    def test_only_undercast_reported(self):
        """ok/overcast 技能不产生问题"""
        gaps = [
            SpellGap(
                name="Frost Strike", spell_id=49143,
                player_casts=50, player_cpm=8.3,
                benchmark_median=45, benchmark_cpm=7.5,
                percentile="p50_p75", verdict="ok",
            ),
        ]
        issues = [g.name for g in gaps if g.verdict == "undercast"]
        assert issues == []


# ============================================================
# 数据模型测试 — DefensiveIssue
# ============================================================
class TestDefensiveIssueModel:
    """DefensiveIssue 模型验证。"""

    def test_unused_defensive(self):
        """未使用防御技能 → unused"""
        issue = DefensiveIssue(
            name="Anti-Magic Shell",
            spell_id=48707,
            player_used=False,
            player_cast_count=0,
            benchmark_usage_rate=85.0,
            verdict="unused",
        )
        assert issue.player_used is False
        assert issue.verdict == "unused"

    def test_ok_defensive(self):
        """使用了防御技能 → ok"""
        issue = DefensiveIssue(
            name="Anti-Magic Shell",
            spell_id=48707,
            player_used=True,
            player_cast_count=3,
            benchmark_usage_rate=85.0,
            verdict="ok",
        )
        assert issue.player_used is True
        assert issue.verdict == "ok"


# ============================================================
# 数据模型测试 — PlayerAnalysisResponse
# ============================================================
class TestPlayerAnalysisResponseModel:
    """PlayerAnalysisResponse 模型结构验证。"""

    def test_full_construction(self):
        """完整构造包含所有字段"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            player_dps=1_200_000,
            dps_percentile="p75_p90",
            fight_duration=300.0,
            player_deaths=1,
            death_times=[92.5],
            rotation_gaps=[
                SpellGap(
                    name="Obliterate", spell_id=49020,
                    player_casts=20, player_cpm=4.0,
                    benchmark_median=25, benchmark_cpm=5.0,
                    percentile="below_p25", verdict="undercast",
                ),
            ],
            cooldown_issues=[
                CooldownIssue(
                    name="Pillar of Frost", spell_id=51271,
                    player_casts=3, benchmark_median_casts=5,
                    missed_uses=2,
                ),
            ],
            defensive_issues=[
                DefensiveIssue(
                    name="Anti-Magic Shell", spell_id=48707,
                    player_used=True, player_cast_count=2,
                    benchmark_usage_rate=85.0, verdict="ok",
                ),
            ],
            build_divergence=BuildDivergence(
                best_match_build=1,
                similarity_pct=90.0,
            ),
            downtime=DowntimeAnalysis(
                active_time_pct=88.0,
                benchmark_active_time_pct=92.0,
                total_downtime_sec=8.0,
                downtime_windows=[
                    DowntimeWindow(start_sec=45.0, end_sec=50.0, duration_sec=5.0),
                ],
                verdict="ok",
            ),
            cd_window_analysis=EventLinkingAnalysis(
                cooldown_windows=[
                    CooldownWindowDetail(
                        buff_name="Pillar of Frost",
                        buff_spell_id=51271,
                        start_sec=10.0,
                        end_sec=22.0,
                        duration_sec=12.0,
                        casts_during=8,
                        density_pct=100.0,
                    ),
                ],
                low_density_windows_count=0,
                verdict="ok",
            ),
            talent_usage=TalentUsageAnalysis(
                talent_gaps=[
                    TalentUsageGap(
                        talent_name="Obliteration",
                        talent_id=1001,
                        spell_name="Obliterate",
                        spell_id=49020,
                        player_casts=20,
                        benchmark_median_casts=25.0,
                        player_cpm=4.0,
                        benchmark_cpm=5.0,
                        verdict="ok",
                    ),
                ],
                unused_talent_spells=[],
            ),
            cd_throughput=[
                CDWindowThroughput(
                    ability_name="Pillar of Frost",
                    window_index=0,
                    damage_done=2_500_000.0,
                    casts_during=8,
                    active_time_pct=95.0,
                    benchmark_median_damage=2_800_000.0,
                    verdict="average",
                ),
            ],
            apl_analysis=None,
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=85.0,
                avg_eclipse_gap_sec=2.5,
                ca_eclipse_coverage_pct=95.0,
                starlord_uptime_pct=70.0,
            ),
            top_issues=["Obliterate 施法不足", "Pillar of Frost 漏用 2 次"],
        )
        assert response.report_code == "ABC123"
        assert response.fight_id == 3
        assert response.player_name == "Frostblade"
        assert response.spec == "frost-death-knight"
        assert response.player_dps == 1_200_000
        assert len(response.rotation_gaps) == 1
        assert len(response.cooldown_issues) == 1
        assert len(response.defensive_issues) == 1
        assert response.build_divergence.similarity_pct == 90.0
        assert len(response.top_issues) == 2
        # Phase 6 新字段
        assert response.downtime is not None
        assert response.downtime.active_time_pct == 88.0
        assert response.cd_window_analysis is not None
        assert len(response.cd_window_analysis.cooldown_windows) == 1
        assert response.talent_usage is not None
        assert len(response.talent_usage.talent_gaps) == 1
        assert len(response.cd_throughput) == 1
        assert response.apl_analysis is None
        # Phase 7 新字段
        assert response.eclipse_metrics is not None
        assert response.eclipse_metrics.eclipse_uptime_pct == 85.0
        assert response.eclipse_metrics.ca_eclipse_coverage_pct == 95.0

    def test_empty_response(self):
        """最小构造（空分析结果）"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.rotation_gaps == []
        assert response.cooldown_issues == []
        assert response.defensive_issues == []
        assert response.top_issues == []
        assert response.build_divergence.similarity_pct == 0.0
        # Phase 6 新字段默认值
        assert response.downtime is None
        assert response.cd_window_analysis is None
        assert response.talent_usage is None
        assert response.cd_throughput == []
        assert response.apl_analysis is None
        # Phase 7 新字段默认值
        assert response.eclipse_metrics is None

    def test_serialization_round_trip(self):
        """model_dump -> 重建 -> 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            encounter_id=3001,
            player_dps=1_200_000,
            rotation_gaps=[
                SpellGap(
                    name="Obliterate", spell_id=49020,
                    player_casts=20, player_cpm=4.0,
                    benchmark_median=25, benchmark_cpm=5.0,
                ),
            ],
            downtime=DowntimeAnalysis(
                active_time_pct=88.0,
                benchmark_active_time_pct=92.0,
                total_downtime_sec=8.0,
                verdict="ok",
            ),
            cd_window_analysis=EventLinkingAnalysis(
                cooldown_windows=[],
                low_density_windows_count=0,
                verdict="ok",
            ),
            talent_usage=TalentUsageAnalysis(
                talent_gaps=[],
                unused_talent_spells=[],
            ),
            cd_throughput=[
                CDWindowThroughput(
                    ability_name="Pillar of Frost",
                    window_index=0,
                    damage_done=2_500_000.0,
                    casts_during=8,
                    active_time_pct=95.0,
                    benchmark_median_damage=2_800_000.0,
                    verdict="average",
                ),
            ],
            apl_analysis=APLAnalysis(
                spec="frost-death-knight",
                compliance_pct=90.0,
            ),
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=82.0,
                avg_eclipse_gap_sec=3.0,
            ),
            top_issues=["Obliterate undercast"],
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert rebuilt.player_dps == original.player_dps
        assert len(rebuilt.rotation_gaps) == 1
        assert rebuilt.rotation_gaps[0].name == "Obliterate"
        assert rebuilt.top_issues == original.top_issues
        # Phase 6 新字段
        assert rebuilt.downtime is not None
        assert rebuilt.downtime.active_time_pct == 88.0
        assert rebuilt.cd_window_analysis is not None
        assert rebuilt.talent_usage is not None
        assert len(rebuilt.cd_throughput) == 1
        assert rebuilt.apl_analysis is not None
        assert rebuilt.apl_analysis.compliance_pct == 90.0
        # Phase 7 新字段
        assert rebuilt.eclipse_metrics is not None
        assert rebuilt.eclipse_metrics.eclipse_uptime_pct == 82.0
        assert rebuilt.eclipse_metrics.avg_eclipse_gap_sec == 3.0


# ============================================================
# 数据模型测试 — EclipseMetrics（Phase 7）
# ============================================================
class TestEclipseMetricsModel:
    """EclipseMetrics 数据模型验证。"""

    def test_valid_construction(self):
        """有效 Eclipse 指标数据通过验证"""
        m = EclipseMetrics(
            eclipse_uptime_pct=85.0,
            avg_eclipse_gap_sec=2.5,
            ca_eclipse_coverage_pct=95.0,
            starlord_uptime_pct=70.0,
        )
        assert m.eclipse_uptime_pct == 85.0
        assert m.avg_eclipse_gap_sec == 2.5
        assert m.ca_eclipse_coverage_pct == 95.0
        assert m.starlord_uptime_pct == 70.0

    def test_defaults(self):
        """默认值正确"""
        m = EclipseMetrics()
        assert m.eclipse_uptime_pct == 0.0
        assert m.avg_eclipse_gap_sec == 0.0
        assert m.ca_eclipse_coverage_pct == 0.0
        assert m.starlord_uptime_pct == 0.0

    def test_serialization_round_trip(self):
        """序列化 -> 重建 -> 字段一致"""
        original = EclipseMetrics(
            eclipse_uptime_pct=85.0,
            avg_eclipse_gap_sec=2.5,
            ca_eclipse_coverage_pct=95.0,
            starlord_uptime_pct=70.0,
        )
        data = original.model_dump()
        rebuilt = EclipseMetrics(**data)
        assert rebuilt.eclipse_uptime_pct == original.eclipse_uptime_pct
        assert rebuilt.avg_eclipse_gap_sec == original.avg_eclipse_gap_sec
        assert rebuilt.ca_eclipse_coverage_pct == original.ca_eclipse_coverage_pct
        assert rebuilt.starlord_uptime_pct == original.starlord_uptime_pct

    def test_partial_construction(self):
        """部分字段构造 -> 其他默认为 0"""
        m = EclipseMetrics(eclipse_uptime_pct=90.0)
        assert m.eclipse_uptime_pct == 90.0
        assert m.avg_eclipse_gap_sec == 0.0
        assert m.ca_eclipse_coverage_pct == 0.0

    def test_in_player_analysis_response(self):
        """PlayerAnalysisResponse 中包含 eclipse_metrics"""
        resp = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="Moonkin",
            spec="balance-druid",
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=85.0,
                avg_eclipse_gap_sec=2.5,
                ca_eclipse_coverage_pct=95.0,
            ),
        )
        assert resp.eclipse_metrics is not None
        assert resp.eclipse_metrics.eclipse_uptime_pct == 85.0

        data = resp.model_dump()
        assert data["eclipse_metrics"]["eclipse_uptime_pct"] == 85.0

    def test_player_analysis_response_eclipse_none(self):
        """PlayerAnalysisResponse 中 eclipse_metrics 为 None"""
        resp = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="Warrior",
            spec="arms-warrior",
        )
        assert resp.eclipse_metrics is None
        data = resp.model_dump()
        assert data["eclipse_metrics"] is None

    def test_full_round_trip_with_eclipse(self):
        """完整 PlayerAnalysisResponse 含 eclipse_metrics 序列化往返"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=1,
            player_name="Moonkin",
            spec="balance-druid",
            eclipse_metrics=EclipseMetrics(
                eclipse_uptime_pct=88.0,
                avg_eclipse_gap_sec=1.8,
                ca_eclipse_coverage_pct=100.0,
                starlord_uptime_pct=65.0,
            ),
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.eclipse_metrics is not None
        assert rebuilt.eclipse_metrics.eclipse_uptime_pct == 88.0
        assert rebuilt.eclipse_metrics.avg_eclipse_gap_sec == 1.8
        assert rebuilt.eclipse_metrics.ca_eclipse_coverage_pct == 100.0
        assert rebuilt.eclipse_metrics.starlord_uptime_pct == 65.0


# ============================================================
# 集成测试 — 完整流程（mock WCL + mock 基准工具）
#
# analyze_player_log 流程:
#   1. 解析 report URL/code
#   2. 查询 WCL 获取战斗数据（fights, events, talents）
#   3. 调用基准工具获取 benchmark 数据
#   4. 对比并生成分析报告
#
# 因为 src.tools.analyze 尚未创建，
# 集成测试使用 pytest.importorskip 策略。
# ============================================================

# 模拟 WCL 战斗信息
ANALYZE_FIGHT_INFO = {
    "reportData": {
        "report": {
            "fights": [
                {
                    "id": 3,
                    "encounterID": 3001,
                    "name": "Vorasius",
                    "startTime": 100_000,
                    "endTime": 400_000,
                    "kill": True,
                    "difficulty": 4,
                }
            ],
        }
    },
}

# 模拟 masterData
ANALYZE_MASTER_DATA = {
    "reportData": {
        "report": {
            "masterData": {
                "actors": [
                    {
                        "id": 5,
                        "name": "Frostblade",
                        "type": "Player",
                        "subType": "DeathKnight",
                    }
                ]
            }
        }
    },
}

# 模拟施法事件
ANALYZE_CAST_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 105_000, "sourceID": 5},
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 115_000, "sourceID": 5},
                    {"type": "cast", "abilityGameID": 49020,
                     "ability": {"name": "Obliterate"},
                     "timestamp": 125_000, "sourceID": 5},
                    {"type": "cast", "abilityGameID": 49143,
                     "ability": {"name": "Frost Strike"},
                     "timestamp": 110_000, "sourceID": 5},
                    {"type": "cast", "abilityGameID": 49143,
                     "ability": {"name": "Frost Strike"},
                     "timestamp": 120_000, "sourceID": 5},
                    {"type": "cast", "abilityGameID": 51271,
                     "ability": {"name": "Pillar of Frost"},
                     "timestamp": 103_000, "sourceID": 5},
                ],
                "nextPageTimestamp": None,
            }
        }
    },
}

# 模拟天赋数据
ANALYZE_TALENT_DATA = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "combatantInfo": {
                        "talentTree": [
                            {"name": "Obliteration", "id": 1001},
                            {"name": "Improved Obliterate", "id": 1002},
                            {"name": "Unleashed Frenzy", "id": 1003},
                        ]
                    }
                }
            }
        }
    },
}

# 模拟死亡事件（无死亡）
ANALYZE_DEATH_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [],
            }
        }
    },
}


class TestAnalyzePlayerLogIntegration:
    """analyze_player_log 端到端集成测试（mock WCL + mock benchmarks）。"""

    @pytest.mark.asyncio
    async def test_basic_analysis(self):
        """应返回有效的 PlayerAnalysisResponse 结构"""
        from src.tools.analyze import analyze_player_log
        from src.models import PlayerAnalysisResponse

        client = MockWCLClient()
        # 配置 mock 响应
        client.set_response("fights", ANALYZE_FIGHT_INFO)
        client.set_response("masterData", ANALYZE_MASTER_DATA)
        client.set_response("dataType: Casts", ANALYZE_CAST_EVENTS)
        client.set_response("dataType: Deaths", ANALYZE_DEATH_EVENTS)
        client.set_response("dataType: Buffs", ANALYZE_TALENT_DATA)
        client.set_response("CombatantInfo", ANALYZE_TALENT_DATA)

        # mock 基准工具 — 用 patch 替换 asyncio.gather 中的工具调用
        with patch("src.tools.analyze.get_rotation_profile") as mock_rot, \
             patch("src.tools.analyze.get_cooldown_timelines") as mock_cd, \
             patch("src.tools.analyze.get_top_builds") as mock_builds, \
             patch("src.tools.analyze.get_defensive_patterns") as mock_def:
            # 返回空/最小响应
            from src.models import RotationProfileResponse, TopBuildsResponse
            mock_rot.return_value = RotationProfileResponse(
                spec="frost-death-knight", encounter_id=3001,
                encounter_name="Test", difficulty="heroic",
            )
            mock_cd.return_value = Exception("skipped")
            mock_builds.return_value = TopBuildsResponse(
                spec="frost-death-knight", encounter_id=3001,
                encounter_name="Test", difficulty="heroic",
            )
            mock_def.return_value = Exception("skipped")

            result = await analyze_player_log(
                client=client,
                report="https://www.warcraftlogs.com/reports/ABC123#fight=3",
                fight_id=3,
                player="Frostblade",
                spec="frost-death-knight",
            )

        assert isinstance(result, PlayerAnalysisResponse)
        assert result.report_code == "ABC123"
        assert result.spec == "frost-death-knight"


# ============================================================
# Bug 修复测试 — 跨职业天赋名称污染（Bug 1）
# ============================================================
class TestBuildCrossClassFiltering:
    """_compare_build 应过滤掉跨职业天赋 ID 碰撞导致的错误名称。"""

    def test_cross_class_talent_filtered(self):
        """
        当 missing 天赋 ID 解析出其他职业的天赋名时，应被过滤掉。

        模拟场景: 某个 talent ID 在 talents.json 中映射到术士天赋，
        但实际应属于德鲁伊——_compare_build 应跳过该条目。
        """
        from src.tools.analyze import _compare_build
        from unittest.mock import MagicMock

        # 构建一个 mock build_bench
        build_bench = MagicMock()
        build1 = MagicMock()
        # 假设基准构建包含天赋 ID 100, 200, 300
        build1.talent_import = "100:1,200:1,300:1"
        build1.usage_pct = 80.0
        build_bench.builds = [build1]

        # 玩家天赋包含 100 和 200，缺少 300
        player_talents = [
            {"id": 100, "talentID": 100},
            {"id": 200, "talentID": 200},
        ]

        # Mock get_talent_spec: ID 300 返回术士专精（"痛苦"），不属于德鲁伊
        with patch("src.tools._analysis_comparisons.get_talent_spec") as mock_spec, \
             patch("src.tools._analysis_comparisons.get_class_spec_names") as mock_names, \
             patch("src.tools._analysis_comparisons.get_talent_name") as mock_name:
            mock_names.return_value = {"平衡", "野性", "守护", "恢复"}
            # ID 300 属于术士（"痛苦"专精），不在德鲁伊专精集合中
            mock_spec.return_value = "痛苦"
            mock_name.return_value = "Unstable Affliction"

            result = _compare_build(
                player_talents, build_bench, spec="balance-druid",
            )

        # 跨职业天赋应被过滤，missing 列表应为空
        assert result.missing_meta_talents == []

    def test_same_class_talent_kept(self):
        """
        当 missing 天赋 ID 属于同职业时，应正常保留。
        """
        from src.tools.analyze import _compare_build
        from unittest.mock import MagicMock

        build_bench = MagicMock()
        build1 = MagicMock()
        build1.talent_import = "100:1,200:1,300:1"
        build1.usage_pct = 80.0
        build_bench.builds = [build1]

        player_talents = [
            {"id": 100, "talentID": 100},
            {"id": 200, "talentID": 200},
        ]

        with patch("src.tools._analysis_comparisons.get_talent_spec") as mock_spec, \
             patch("src.tools._analysis_comparisons.get_class_spec_names") as mock_names, \
             patch("src.tools._analysis_comparisons.get_talent_name") as mock_name:
            mock_names.return_value = {"平衡", "野性", "守护", "恢复"}
            # ID 300 属于德鲁伊（"平衡"专精）
            mock_spec.return_value = "平衡"
            mock_name.side_effect = lambda tid, lang="zh": (
                "星涌术" if lang == "zh" else "Starsurge"
            )

            result = _compare_build(
                player_talents, build_bench, spec="balance-druid",
            )

        # 同职业天赋应保留
        assert len(result.missing_meta_talents) == 1
        assert "Starsurge" in result.missing_meta_talents[0]


# ============================================================
# Bug 修复测试 — 互斥天赋 CD 误报（Bug 2）
# ============================================================
class TestCooldownMutualExclusion:
    """_compare_cooldowns 应跳过玩家未选择的天赋授予技能。"""

    def test_convoke_skipped_for_incarnation_player(self):
        """
        玩家选择了 Incarnation（102560），基准含 Convoke（391528）——
        不应标记 Convoke 为 missed。
        """
        from src.tools.analyze import _compare_cooldowns
        from unittest.mock import MagicMock

        # 构建基准 timeline: 含 Convoke 3 次中位
        timeline_bench = MagicMock()
        convoke_ability = MagicMock()
        convoke_ability.name = "Convoke the Spirits"
        convoke_ability.total_casts = {"median": 3.0, "min": 2.0, "max": 4.0}
        timeline_bench.abilities = [convoke_ability]

        # 玩家没有使用 Convoke
        player_spell_counts: dict[int, int] = {}
        player_spell_names: dict[int, str] = {}

        # 玩家天赋包含 Incarnation（talent entry → spell_id 102560）
        # 不包含 Convoke 对应的天赋
        player_talents = [
            {"id": 88206, "talentID": 88206},  # Incarnation talent entry
        ]

        with patch("src.tools._analysis_comparisons.get_talent_spell_id") as mock_tsid, \
             patch("src.tools._analysis_comparisons.get_talent_id_by_spell") as mock_tid, \
             patch("src.tools._analysis_comparisons.get_spec_spells") as mock_spells:
            # Incarnation talent entry 88206 → spell_id 102560
            mock_tsid.return_value = 102560
            # Convoke spell_id 391528 → 有对应天赋条目
            mock_tid.return_value = 99999  # 某个天赋条目 ID
            # spec 技能列表含 Convoke
            mock_spells.return_value = [
                {"name": "Convoke the Spirits", "spell_id": 391528},
            ]

            issues = _compare_cooldowns(
                player_spell_counts,
                player_spell_names,
                timeline_bench,
                player_talents=player_talents,
                spec="balance-druid",
            )

        # Convoke 不应出现在 issues 中
        assert len(issues) == 0

    def test_convoke_kept_for_convoke_player(self):
        """
        玩家选择了 Convoke（391528），基准含 Convoke 3 次——
        若玩家 0 次使用，应正常标记。
        """
        from src.tools.analyze import _compare_cooldowns
        from unittest.mock import MagicMock

        timeline_bench = MagicMock()
        convoke_ability = MagicMock()
        convoke_ability.name = "Convoke the Spirits"
        convoke_ability.total_casts = {"median": 3.0, "min": 2.0, "max": 4.0}
        timeline_bench.abilities = [convoke_ability]

        player_spell_counts: dict[int, int] = {}
        player_spell_names: dict[int, str] = {}

        # 玩家天赋包含 Convoke 对应天赋
        player_talents = [
            {"id": 77777, "talentID": 77777},  # Convoke talent entry
        ]

        with patch("src.tools._analysis_comparisons.get_talent_spell_id") as mock_tsid, \
             patch("src.tools._analysis_comparisons.get_talent_id_by_spell") as mock_tid, \
             patch("src.tools._analysis_comparisons.get_spec_spells") as mock_spells:
            # Convoke talent entry 77777 → spell_id 391528
            mock_tsid.return_value = 391528
            # Convoke spell_id 391528 → 有对应天赋条目
            mock_tid.return_value = 77777
            mock_spells.return_value = [
                {"name": "Convoke the Spirits", "spell_id": 391528},
            ]

            issues = _compare_cooldowns(
                player_spell_counts,
                player_spell_names,
                timeline_bench,
                player_talents=player_talents,
                spec="balance-druid",
            )

        # 玩家有 Convoke 天赋但 0 次使用 → 应标记
        assert len(issues) == 1
        assert issues[0].name == "Convoke the Spirits"
        assert issues[0].missed_uses == 3

    def test_non_talent_cd_always_compared(self):
        """
        非天赋授予的 CD 技能（如 Celestial Alignment）应始终对比。
        """
        from src.tools.analyze import _compare_cooldowns
        from unittest.mock import MagicMock

        timeline_bench = MagicMock()
        ca_ability = MagicMock()
        ca_ability.name = "Celestial Alignment"
        ca_ability.total_casts = {"median": 2.0, "min": 1.0, "max": 3.0}
        timeline_bench.abilities = [ca_ability]

        # 玩家施法记录中有该技能但只用了 0 次
        player_spell_counts: dict[int, int] = {383410: 0}
        player_spell_names: dict[int, str] = {383410: "Celestial Alignment"}

        player_talents = [{"id": 88206, "talentID": 88206}]

        with patch("src.tools._analysis_comparisons.get_talent_spell_id") as mock_tsid, \
             patch("src.tools._analysis_comparisons.get_talent_id_by_spell") as mock_tid, \
             patch("src.tools._analysis_comparisons.get_spec_spells") as mock_spells:
            mock_tsid.return_value = 102560
            # Celestial Alignment 非天赋授予技能
            mock_tid.return_value = None
            mock_spells.return_value = [
                {"name": "Celestial Alignment", "spell_id": 383410},
            ]

            issues = _compare_cooldowns(
                player_spell_counts,
                player_spell_names,
                timeline_bench,
                player_talents=player_talents,
                spec="balance-druid",
            )

        # 非天赋技能应正常对比
        assert len(issues) == 1
        assert issues[0].name == "Celestial Alignment"
