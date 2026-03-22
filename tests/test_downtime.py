# ============================================================
# Phase 6A: Downtime/GCD 分析测试
# 覆盖模型验证、停工窗口检测、活跃时间计算、基准对比
#
# 测试策略:
#   - 纯单元测试: 停工窗口检测、活跃时间计算（不依赖实现）
#   - 模型测试: DowntimeWindow, DowntimeAnalysis 验证
#   - 集成测试: PlayerAnalysisResponse 包含 downtime 字段
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    DowntimeAnalysis,
    DowntimeWindow,
    PlayerAnalysisResponse,
)


# ============================================================
# 辅助函数 — 停工窗口检测（纯逻辑，镜像 analyze 模块预期行为）
# ============================================================
def _calc_active_time_pct(fight_duration: float, total_downtime: float) -> float:
    """活跃时间百分比计算。"""
    if fight_duration <= 0:
        return 0.0
    return (fight_duration - total_downtime) / fight_duration * 100.0


def _find_downtime_gaps(
    cast_timestamps_ms: list[int],
    fight_start_ms: int,
    fight_end_ms: int,
    gap_threshold_sec: float = 2.0,
) -> list[tuple[float, float, float]]:
    """
    找出超过阈值的停工窗口。

    返回 (start_sec, end_sec, duration_sec) 列表，
    其中时间为相对于 fight_start 的秒数。
    gap 必须严格大于 threshold 才算停工。
    """
    if fight_end_ms <= fight_start_ms:
        return []

    sorted_ts = sorted(cast_timestamps_ms)
    gaps: list[tuple[float, float, float]] = []

    # 检查战斗开始到第一次施法的间隙
    if not sorted_ts:
        # 没有施法 → 整个战斗都是停工
        duration_sec = (fight_end_ms - fight_start_ms) / 1000.0
        if duration_sec > gap_threshold_sec:
            gaps.append((0.0, duration_sec, duration_sec))
        return gaps

    # 战斗开始到第一次施法
    first_gap_ms = sorted_ts[0] - fight_start_ms
    first_gap_sec = first_gap_ms / 1000.0
    if first_gap_sec > gap_threshold_sec:
        gaps.append((0.0, first_gap_sec, first_gap_sec))

    # 相邻施法之间的间隙
    for i in range(1, len(sorted_ts)):
        gap_ms = sorted_ts[i] - sorted_ts[i - 1]
        gap_sec = gap_ms / 1000.0
        if gap_sec > gap_threshold_sec:
            start_sec = (sorted_ts[i - 1] - fight_start_ms) / 1000.0
            end_sec = (sorted_ts[i] - fight_start_ms) / 1000.0
            gaps.append((start_sec, end_sec, gap_sec))

    # 最后一次施法到战斗结束
    last_gap_ms = fight_end_ms - sorted_ts[-1]
    last_gap_sec = last_gap_ms / 1000.0
    if last_gap_sec > gap_threshold_sec:
        start_sec = (sorted_ts[-1] - fight_start_ms) / 1000.0
        end_sec = (fight_end_ms - fight_start_ms) / 1000.0
        gaps.append((start_sec, end_sec, last_gap_sec))

    return gaps


def _estimate_benchmark_active_time(
    top_spells_total_casts: list[float],
    fight_duration_median: float,
    gcd: float = 1.0,
    cap: float = 95.0,
) -> float:
    """基准活跃时间估算（有效 GCD 1.0s，上限 95%）。"""
    if fight_duration_median <= 0:
        return 0.0
    if not top_spells_total_casts:
        return 0.0
    total_gcds = sum(top_spells_total_casts)
    active_time = total_gcds * gcd
    return min(active_time / fight_duration_median * 100.0, cap)


def _downtime_verdict(player_pct: float, benchmark_pct: float) -> str:
    """根据活跃时间百分比差距判定 verdict。"""
    diff = benchmark_pct - player_pct
    if diff <= 5.0:
        return "ok"
    if diff <= 15.0:
        return "low_activity"
    return "very_low_activity"


# ============================================================
# 模型测试 — DowntimeWindow
# ============================================================
class TestDowntimeWindowModel:
    """DowntimeWindow 数据模型验证。"""

    def test_valid_construction(self):
        """有效停工窗口数据通过验证"""
        w = DowntimeWindow(start_sec=10.0, end_sec=15.0, duration_sec=5.0)
        assert w.start_sec == 10.0
        assert w.end_sec == 15.0
        assert w.duration_sec == 5.0

    def test_missing_start_sec_raises(self):
        """缺少 start_sec 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeWindow(end_sec=15.0, duration_sec=5.0)  # type: ignore

    def test_missing_end_sec_raises(self):
        """缺少 end_sec 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeWindow(start_sec=10.0, duration_sec=5.0)  # type: ignore

    def test_missing_duration_sec_raises(self):
        """缺少 duration_sec 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeWindow(start_sec=10.0, end_sec=15.0)  # type: ignore

    def test_serialization_round_trip(self):
        """序列化 → 重建 → 字段一致"""
        original = DowntimeWindow(start_sec=10.0, end_sec=15.0, duration_sec=5.0)
        data = original.model_dump()
        rebuilt = DowntimeWindow(**data)
        assert rebuilt.start_sec == original.start_sec
        assert rebuilt.end_sec == original.end_sec
        assert rebuilt.duration_sec == original.duration_sec


# ============================================================
# 模型测试 — DowntimeAnalysis
# ============================================================
class TestDowntimeAnalysisModel:
    """DowntimeAnalysis 数据模型验证。"""

    def test_valid_construction(self):
        """有效停工分析数据通过验证"""
        analysis = DowntimeAnalysis(
            active_time_pct=85.0,
            benchmark_active_time_pct=92.0,
            total_downtime_sec=12.5,
            downtime_windows=[
                DowntimeWindow(start_sec=30.0, end_sec=37.5, duration_sec=7.5),
                DowntimeWindow(start_sec=60.0, end_sec=65.0, duration_sec=5.0),
            ],
            verdict="low_activity",
        )
        assert analysis.active_time_pct == 85.0
        assert analysis.benchmark_active_time_pct == 92.0
        assert analysis.total_downtime_sec == 12.5
        assert len(analysis.downtime_windows) == 2
        assert analysis.verdict == "low_activity"

    def test_missing_required_active_time_pct(self):
        """缺少 active_time_pct 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeAnalysis(
                benchmark_active_time_pct=92.0,
                total_downtime_sec=5.0,
            )  # type: ignore

    def test_missing_required_benchmark(self):
        """缺少 benchmark_active_time_pct 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeAnalysis(
                active_time_pct=85.0,
                total_downtime_sec=5.0,
            )  # type: ignore

    def test_missing_required_total_downtime(self):
        """缺少 total_downtime_sec 被拒绝"""
        with pytest.raises(ValidationError):
            DowntimeAnalysis(
                active_time_pct=85.0,
                benchmark_active_time_pct=92.0,
            )  # type: ignore

    def test_empty_downtime_windows(self):
        """空 downtime_windows 列表有效"""
        analysis = DowntimeAnalysis(
            active_time_pct=95.0,
            benchmark_active_time_pct=92.0,
            total_downtime_sec=0.0,
            downtime_windows=[],
            verdict="ok",
        )
        assert analysis.downtime_windows == []

    def test_verdict_defaults_to_empty(self):
        """verdict 默认为空字符串"""
        analysis = DowntimeAnalysis(
            active_time_pct=90.0,
            benchmark_active_time_pct=92.0,
            total_downtime_sec=3.0,
        )
        assert analysis.verdict == ""

    def test_serialization_round_trip(self):
        """序列化 → 重建 → 字段一致"""
        original = DowntimeAnalysis(
            active_time_pct=85.0,
            benchmark_active_time_pct=92.0,
            total_downtime_sec=10.0,
            downtime_windows=[
                DowntimeWindow(start_sec=5.0, end_sec=10.0, duration_sec=5.0),
            ],
            verdict="low_activity",
        )
        data = original.model_dump()
        rebuilt = DowntimeAnalysis(**data)
        assert rebuilt.active_time_pct == original.active_time_pct
        assert rebuilt.benchmark_active_time_pct == original.benchmark_active_time_pct
        assert rebuilt.total_downtime_sec == original.total_downtime_sec
        assert len(rebuilt.downtime_windows) == 1
        assert rebuilt.verdict == original.verdict


# ============================================================
# 单元测试 — 活跃时间百分比计算
# ============================================================
class TestCalcActiveTimePct:
    """活跃时间百分比计算逻辑。"""

    def test_no_downtime(self):
        """无停工 → 100% 活跃"""
        assert _calc_active_time_pct(300.0, 0.0) == 100.0

    def test_half_downtime(self):
        """一半停工 → 50% 活跃"""
        assert _calc_active_time_pct(300.0, 150.0) == 50.0

    def test_full_downtime(self):
        """全部停工 → 0% 活跃"""
        assert _calc_active_time_pct(300.0, 300.0) == 0.0

    def test_zero_fight_duration(self):
        """战斗时长为 0 → 0% 活跃"""
        assert _calc_active_time_pct(0.0, 0.0) == 0.0

    def test_negative_fight_duration(self):
        """负数战斗时长 → 0%"""
        assert _calc_active_time_pct(-10.0, 0.0) == 0.0

    def test_partial_downtime(self):
        """部分停工 → 正确百分比"""
        result = _calc_active_time_pct(200.0, 30.0)
        assert abs(result - 85.0) < 0.01


# ============================================================
# 单元测试 — 停工窗口检测
# ============================================================
class TestFindDowntimeGaps:
    """停工窗口检测逻辑。"""

    def test_no_gaps(self):
        """每 1.5 秒施法一次 → 无停工窗口"""
        fight_start = 0
        fight_end = 30_000  # 30 秒
        # 每 1500ms 施法一次
        casts = list(range(0, 30_001, 1500))
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        assert gaps == []

    def test_single_large_gap(self):
        """中间有 10 秒间隙 → 检测到一个停工窗口"""
        fight_start = 0
        fight_end = 16_000
        # 0s, 1s, 2s, 然后 12s 后才有下一次施法, 持续到战斗结束
        casts = [0, 1000, 2000, 12000, 13000, 14000, 15000, 16000]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        # 2s → 12s 是 10 秒间隙
        assert len(gaps) == 1
        assert abs(gaps[0][2] - 10.0) < 0.01

    def test_multiple_gaps(self):
        """多个间隙 → 检测到多个停工窗口"""
        fight_start = 0
        fight_end = 50_000
        # 0s, 1s, 然后 8s, 9s, 然后 20s, 21s
        casts = [0, 1000, 8000, 9000, 20000, 21000]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        # 间隙: 1s→8s (7s), 9s→20s (11s), 21s→50s (29s)
        assert len(gaps) == 3

    def test_empty_cast_list(self):
        """无施法 → 整个战斗都是停工"""
        fight_start = 0
        fight_end = 30_000
        gaps = _find_downtime_gaps([], fight_start, fight_end)
        assert len(gaps) == 1
        assert abs(gaps[0][2] - 30.0) < 0.01

    def test_single_cast(self):
        """一次施法 → 检查开始和结束间隙"""
        fight_start = 0
        fight_end = 30_000
        casts = [15_000]  # 15 秒处施法
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        # 0→15s (15s) 和 15s→30s (15s)
        assert len(gaps) == 2
        assert abs(gaps[0][2] - 15.0) < 0.01
        assert abs(gaps[1][2] - 15.0) < 0.01

    def test_gap_at_start(self):
        """战斗开始 5 秒后才有第一次施法 → 检测到开始间隙"""
        fight_start = 0
        fight_end = 20_000
        casts = [5000, 6000, 7000, 8000, 9000, 10000]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        # 开始间隙: 0→5s (5s)，结束间隙: 10s→20s (10s)
        assert len(gaps) == 2
        assert abs(gaps[0][2] - 5.0) < 0.01

    def test_gap_at_end(self):
        """最后一次施法后 5 秒战斗才结束 → 检测到结束间隙"""
        fight_start = 0
        fight_end = 20_000
        casts = [0, 1000, 2000, 3000, 14000, 15000]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        # 结束间隙: 15s→20s (5s)，中间间隙: 3s→14s (11s)
        assert len(gaps) == 2

    def test_exactly_at_threshold_not_downtime(self):
        """恰好 2.0 秒间隙 → 不算停工（必须严格大于阈值）"""
        fight_start = 0
        fight_end = 10_000
        # 施法 0s, 2s, 4s, 6s, 8s → 间隙恰好 2.0s
        casts = [0, 2000, 4000, 6000, 8000, 10000]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end)
        assert gaps == []

    def test_just_over_threshold_is_downtime(self):
        """2.1 秒间隙 → 算停工"""
        fight_start = 0
        fight_end = 10_000
        casts = [0, 2100]
        gaps = _find_downtime_gaps(casts, fight_start, fight_end, gap_threshold_sec=2.0)
        # 间隙: 0→2.1s (2.1s)，2.1s→10s (7.9s)
        assert len(gaps) == 2
        assert abs(gaps[0][2] - 2.1) < 0.01


# ============================================================
# 单元测试 — 基准活跃时间估算
# ============================================================
class TestEstimateBenchmarkActiveTime:
    """基准活跃时间估算逻辑。"""

    def test_normal_case(self):
        """正常情况 → 合理百分比"""
        # 100 次施法 × 1.0 GCD = 100 秒 / 300 秒 = 33.3%
        result = _estimate_benchmark_active_time([100.0], 300.0)
        assert abs(result - 33.33) < 0.1

    def test_multiple_spells(self):
        """多个技能 → 施法次数求和"""
        # (50 + 30 + 20) × 1.0 = 100 秒 / 300 秒 = 33.3%
        result = _estimate_benchmark_active_time([50.0, 30.0, 20.0], 300.0)
        assert abs(result - 33.33) < 0.1

    def test_zero_fight_duration(self):
        """战斗时长为 0 → 0%"""
        result = _estimate_benchmark_active_time([100.0], 0.0)
        assert result == 0.0

    def test_no_spells(self):
        """无技能数据 → 0%"""
        result = _estimate_benchmark_active_time([], 300.0)
        assert result == 0.0

    def test_capped_at_95(self):
        """超过上限 → 95%（顶级玩家也无法 100% 活跃）"""
        # 1000 次 × 1.0 = 1000 秒 / 100 秒 = 超过 95%
        result = _estimate_benchmark_active_time([1000.0], 100.0)
        assert result == 95.0


# ============================================================
# 单元测试 — Verdict 判定逻辑
# ============================================================
class TestDowntimeVerdict:
    """停工 verdict 判定逻辑。"""

    def test_within_5pct_is_ok(self):
        """差距 <=5% → ok"""
        assert _downtime_verdict(87.0, 92.0) == "ok"
        assert _downtime_verdict(92.0, 92.0) == "ok"

    def test_exactly_5pct_is_ok(self):
        """差距恰好 5% → ok"""
        assert _downtime_verdict(87.0, 92.0) == "ok"

    def test_6_to_15pct_is_low_activity(self):
        """差距 5-15% → low_activity"""
        assert _downtime_verdict(80.0, 92.0) == "low_activity"
        assert _downtime_verdict(77.5, 92.0) == "low_activity"

    def test_over_15pct_is_very_low_activity(self):
        """差距 >15% → very_low_activity"""
        assert _downtime_verdict(70.0, 92.0) == "very_low_activity"
        assert _downtime_verdict(50.0, 92.0) == "very_low_activity"

    def test_player_above_benchmark_is_ok(self):
        """玩家高于基准 → ok"""
        assert _downtime_verdict(95.0, 92.0) == "ok"
        assert _downtime_verdict(100.0, 90.0) == "ok"


# ============================================================
# 集成测试 — PlayerAnalysisResponse 包含 downtime 字段
# ============================================================
class TestPlayerAnalysisResponseDowntime:
    """PlayerAnalysisResponse 中 downtime 字段集成测试。"""

    def test_with_downtime_populated(self):
        """构造包含 downtime 的 PlayerAnalysisResponse → 序列化正确"""
        response = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            downtime=DowntimeAnalysis(
                active_time_pct=85.0,
                benchmark_active_time_pct=92.0,
                total_downtime_sec=12.5,
                downtime_windows=[
                    DowntimeWindow(start_sec=30.0, end_sec=37.5, duration_sec=7.5),
                ],
                verdict="low_activity",
            ),
        )
        assert response.downtime is not None
        assert response.downtime.active_time_pct == 85.0
        assert len(response.downtime.downtime_windows) == 1

        # 序列化验证
        data = response.model_dump()
        assert data["downtime"]["active_time_pct"] == 85.0
        assert len(data["downtime"]["downtime_windows"]) == 1

    def test_with_downtime_none(self):
        """downtime=None → 可选字段，序列化为 None"""
        response = PlayerAnalysisResponse(
            report_code="XYZ789",
            fight_id=1,
            player_name="TestPlayer",
            spec="frost-death-knight",
        )
        assert response.downtime is None
        data = response.model_dump()
        assert data["downtime"] is None

    def test_full_round_trip_with_downtime(self):
        """model_dump → 重建 → 完整 downtime 字段一致"""
        original = PlayerAnalysisResponse(
            report_code="ABC123",
            fight_id=3,
            player_name="Frostblade",
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            player_dps=1_200_000,
            fight_duration=300.0,
            downtime=DowntimeAnalysis(
                active_time_pct=88.0,
                benchmark_active_time_pct=92.0,
                total_downtime_sec=8.0,
                downtime_windows=[
                    DowntimeWindow(start_sec=45.0, end_sec=50.0, duration_sec=5.0),
                    DowntimeWindow(start_sec=120.0, end_sec=123.0, duration_sec=3.0),
                ],
                verdict="ok",
            ),
            top_issues=["Obliterate undercast"],
        )
        data = original.model_dump()
        rebuilt = PlayerAnalysisResponse(**data)
        assert rebuilt.downtime is not None
        assert rebuilt.downtime.active_time_pct == 88.0
        assert rebuilt.downtime.benchmark_active_time_pct == 92.0
        assert len(rebuilt.downtime.downtime_windows) == 2
        assert rebuilt.downtime.verdict == "ok"


# ============================================================
# 单元测试 — top_issues 中的 downtime 标记
# ============================================================
class TestDowntimeTopIssues:
    """停工分析对 top_issues 的影响。"""

    def test_low_activity_triggers_issue(self):
        """活跃时间 >5% 低于基准 → 触发 top_issues 条目"""
        player_pct = 80.0
        benchmark_pct = 92.0
        verdict = _downtime_verdict(player_pct, benchmark_pct)
        assert verdict in ("low_activity", "very_low_activity")

        # 模拟 top_issues 生成逻辑
        issues: list[str] = []
        if verdict != "ok":
            issues.append(
                f"活跃时间偏低 ({player_pct:.0f}% vs 基准 {benchmark_pct:.0f}%)"
            )
        assert len(issues) == 1
        assert "活跃时间偏低" in issues[0]

    def test_ok_does_not_trigger_issue(self):
        """活跃时间正常 → 不产生 top_issues 条目"""
        player_pct = 90.0
        benchmark_pct = 92.0
        verdict = _downtime_verdict(player_pct, benchmark_pct)
        assert verdict == "ok"

        issues: list[str] = []
        if verdict != "ok":
            issues.append("活跃时间偏低")
        assert issues == []

    def test_very_low_triggers_issue(self):
        """非常低的活跃时间 → 同样触发 top_issues"""
        player_pct = 60.0
        benchmark_pct = 92.0
        verdict = _downtime_verdict(player_pct, benchmark_pct)
        assert verdict == "very_low_activity"

        issues: list[str] = []
        if verdict != "ok":
            issues.append(
                f"活跃时间严重偏低 ({player_pct:.0f}% vs 基准 {benchmark_pct:.0f}%)"
            )
        assert len(issues) == 1
