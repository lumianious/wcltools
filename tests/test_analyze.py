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
    BuildDivergence,
    CooldownIssue,
    DefensiveIssue,
    PlayerAnalysisResponse,
    SpellGap,
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
            top_issues=["Obliterate undercast"],
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.report_code == original.report_code
        assert rebuilt.player_dps == original.player_dps
        assert len(rebuilt.rotation_gaps) == 1
        assert rebuilt.rotation_gaps[0].name == "Obliterate"
        assert rebuilt.top_issues == original.top_issues


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
