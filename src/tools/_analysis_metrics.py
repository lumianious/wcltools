"""
玩家日志分析指标 — 死亡、停工、CD 窗口、Eclipse、问题归纳。

从 analyze_player_log 主模块拆分，负责计算各维度的分析指标，
以及从全部指标中归纳 Top Issues。

公开接口:
  - analyze_deaths(...) -> tuple[int, list[float]]
  - analyze_downtime(...) -> DowntimeAnalysis
  - analyze_cd_windows(...) -> EventLinkingAnalysis
  - analyze_eclipse_metrics(...) -> Optional[EclipseMetrics]
  - summarize_top_issues(...) -> list[str]

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.data import get_spec_spells
from src.models import (
    BuildDivergence,
    CDWindowThroughput,
    CooldownIssue,
    CooldownWindowDetail,
    DefensiveIssue,
    DowntimeAnalysis,
    DowntimeWindow,
    EclipseMetrics,
    EventLinkingAnalysis,
    SpellGap,
    TalentUsageAnalysis,
)

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
_DOWNTIME_GAP_THRESHOLD = 2.0       # 秒，超过此值视为停工窗口
_CD_DENSITY_THRESHOLD = 0.70        # 密度低于 70% 视为低效窗口
_EFFECTIVE_GCD = 1.0                # 有效 GCD（考虑急速）
_MAX_BENCHMARK_ACTIVE_PCT = 95.0    # 基准活跃上限


# ============================================================
# 死亡分析
# ============================================================


def analyze_deaths(
    death_events: list[dict],
    start_time: int,
) -> tuple[int, list[float]]:
    """返回 (death_count, death_times_relative_seconds)。"""
    death_times: list[float] = []
    for evt in death_events:
        ts = evt.get("timestamp", 0)
        relative_sec = round((ts - start_time) / 1000.0, 1)
        death_times.append(relative_sec)
    return len(death_times), death_times


# ============================================================
# 停工 / GCD 分析
# ============================================================


def _merge_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """合并重叠/相邻的活动区间。"""
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged: list[tuple[int, int]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def analyze_downtime(
    activity_intervals: list[tuple[int, int]],
    fight_duration: float,
    fight_start_time: int,
    rotation_bench: Any,
) -> DowntimeAnalysis:
    """根据活动区间计算停工时间窗口。"""
    if fight_duration <= 0:
        return DowntimeAnalysis(
            active_time_pct=0.0,
            benchmark_active_time_pct=0.0,
            total_downtime_sec=0.0,
            verdict="ok",
        )

    fight_end_time = fight_start_time + int(fight_duration * 1000)
    merged = _merge_intervals(activity_intervals)

    downtime_windows, total_downtime = _find_downtime_windows(
        merged, fight_start_time, fight_end_time, fight_duration,
    )

    active_time_pct = round(
        (fight_duration - total_downtime) / fight_duration * 100, 1,
    )
    benchmark_pct = _compute_benchmark_active_pct(rotation_bench)
    verdict = _compute_downtime_verdict(active_time_pct, benchmark_pct)

    return DowntimeAnalysis(
        active_time_pct=active_time_pct,
        benchmark_active_time_pct=benchmark_pct,
        total_downtime_sec=round(total_downtime, 1),
        downtime_windows=downtime_windows,
        verdict=verdict,
    )


def _find_downtime_windows(
    merged: list[tuple[int, int]],
    fight_start: int,
    fight_end: int,
    fight_duration: float,
) -> tuple[list[DowntimeWindow], float]:
    """从合并后的活动区间中提取停工窗口。"""
    windows: list[DowntimeWindow] = []
    total = 0.0

    if not merged:
        total = fight_duration
        windows.append(DowntimeWindow(
            start_sec=0.0,
            end_sec=round(fight_duration, 2),
            duration_sec=round(fight_duration, 2),
        ))
        return windows, total

    # 战斗开始到第一个活动区间
    first_gap = (merged[0][0] - fight_start) / 1000.0
    if first_gap > _DOWNTIME_GAP_THRESHOLD:
        total += first_gap
        windows.append(DowntimeWindow(
            start_sec=0.0,
            end_sec=round(first_gap, 2),
            duration_sec=round(first_gap, 2),
        ))

    # 合并后区间之间
    for i in range(1, len(merged)):
        gap = (merged[i][0] - merged[i - 1][1]) / 1000.0
        if gap > _DOWNTIME_GAP_THRESHOLD:
            start_sec = (merged[i - 1][1] - fight_start) / 1000.0
            end_sec = (merged[i][0] - fight_start) / 1000.0
            total += gap
            windows.append(DowntimeWindow(
                start_sec=round(start_sec, 2),
                end_sec=round(end_sec, 2),
                duration_sec=round(gap, 2),
            ))

    # 最后一个活动区间到战斗结束
    last_gap = (fight_end - merged[-1][1]) / 1000.0
    if last_gap > _DOWNTIME_GAP_THRESHOLD:
        start_sec = (merged[-1][1] - fight_start) / 1000.0
        total += last_gap
        windows.append(DowntimeWindow(
            start_sec=round(start_sec, 2),
            end_sec=round(fight_duration, 2),
            duration_sec=round(last_gap, 2),
        ))

    return windows, total


def _compute_benchmark_active_pct(rotation_bench: Any) -> float:
    """从基准数据计算基准活跃时间百分比。"""
    if rotation_bench is None or not hasattr(rotation_bench, "top_spells"):
        return 0.0
    bench_duration = getattr(rotation_bench, "fight_duration_median", 0.0)
    if bench_duration <= 0:
        return 0.0
    total_casts = sum(s.total_casts for s in rotation_bench.top_spells)
    return round(
        min(total_casts * _EFFECTIVE_GCD / bench_duration * 100,
            _MAX_BENCHMARK_ACTIVE_PCT),
        1,
    )


def _compute_downtime_verdict(
    active_pct: float,
    benchmark_pct: float,
) -> str:
    """根据活跃时间与基准的差距判定。"""
    if benchmark_pct > 0:
        diff = benchmark_pct - active_pct
        if diff > 15:
            return "very_low_activity"
        elif diff > 5:
            return "low_activity"
        return "ok"
    # 无基准时凭绝对值
    if active_pct < 70:
        return "very_low_activity"
    elif active_pct < 85:
        return "low_activity"
    return "ok"


# ============================================================
# CD 窗口事件关联
# ============================================================


def analyze_cd_windows(
    cast_timestamps: list[tuple[int, int]],
    buff_uptimes: list[dict],
    fight_start_time: int,
    fight_duration: float,
    spec: str,
) -> EventLinkingAnalysis:
    """关联玩家施法与 CD Buff 窗口，分析施法密度。"""
    if not cast_timestamps or not buff_uptimes or fight_duration <= 0:
        return EventLinkingAnalysis(verdict="ok")

    cd_spells = _get_cd_spells(spec)
    if not cd_spells:
        return EventLinkingAnalysis(verdict="ok")

    sorted_casts = sorted(cast_timestamps, key=lambda x: x[0])
    windows, low_count = _evaluate_cd_windows(
        sorted_casts, buff_uptimes, cd_spells, fight_start_time,
    )

    verdict = "low_density_burst" if low_count > 0 else "ok"
    return EventLinkingAnalysis(
        cooldown_windows=windows,
        low_density_windows_count=low_count,
        verdict=verdict,
    )


def _get_cd_spells(spec: str) -> dict[int, str]:
    """获取需要追踪的 CD 技能。"""
    cd_spells: dict[int, str] = {}
    for s in get_spec_spells(spec):
        cd = s.get("cooldown", 0)
        tags = s.get("tags", [])
        if cd >= 30 and ("dps" in tags or "raid_cd" in tags):
            sid = s.get("spell_id")
            if sid:
                cd_spells[sid] = s.get("name", f"Spell {sid}")
    return cd_spells


def _evaluate_cd_windows(
    sorted_casts: list[tuple[int, int]],
    buff_uptimes: list[dict],
    cd_spells: dict[int, str],
    fight_start_time: int,
) -> tuple[list[CooldownWindowDetail], int]:
    """评估所有 CD 窗口的施法密度。"""
    windows: list[CooldownWindowDetail] = []
    low_count = 0

    for aura in buff_uptimes:
        aura_id = aura.get("id") or aura.get("guid")
        if aura_id not in cd_spells:
            continue

        buff_name = cd_spells[aura_id]
        for band in aura.get("bands", []):
            window = _evaluate_single_window(
                band, buff_name, aura_id,
                sorted_casts, fight_start_time,
            )
            if window is None:
                continue
            if window.density_pct < _CD_DENSITY_THRESHOLD * 100:
                low_count += 1
            windows.append(window)

    return windows, low_count


def _evaluate_single_window(
    band: dict,
    buff_name: str,
    aura_id: int,
    sorted_casts: list[tuple[int, int]],
    fight_start_time: int,
) -> Optional[CooldownWindowDetail]:
    """评估单个 CD 窗口的施法密度。"""
    band_start = band.get("startTime", 0)
    band_end = band.get("endTime", 0)
    if band_start >= band_end:
        return None

    start_sec = (band_start - fight_start_time) / 1000.0
    end_sec = (band_end - fight_start_time) / 1000.0
    duration_sec = end_sec - start_sec
    if duration_sec < 1.0:
        return None

    casts_during = sum(
        1 for ts, _ in sorted_casts
        if band_start <= ts <= band_end
    )
    max_gcds = duration_sec / _EFFECTIVE_GCD
    density = casts_during / max_gcds if max_gcds > 0 else 0.0

    return CooldownWindowDetail(
        buff_name=buff_name,
        buff_spell_id=aura_id,
        start_sec=round(start_sec, 2),
        end_sec=round(end_sec, 2),
        duration_sec=round(duration_sec, 2),
        casts_during=casts_during,
        density_pct=round(density * 100, 1),
    )


# ============================================================
# Eclipse 指标（Balance Druid 专用）
# ============================================================

_ECLIPSE_BUFF_KEYWORDS = ["eclipse"]
_STARLORD_BUFF_KEYWORDS = ["starlord"]
_CA_BUFF_KEYWORDS = ["celestial alignment", "incarnation"]


def analyze_eclipse_metrics(
    buff_uptimes: list[dict],
    fight_duration: float,
) -> Optional[EclipseMetrics]:
    """从 Buff 覆盖率数据中提取 Eclipse 相关指标。"""
    if fight_duration <= 0 or not buff_uptimes:
        return None

    fight_dur_ms = fight_duration * 1000.0
    eclipse_ms, starlord_ms, ca_ms = 0.0, 0.0, 0.0
    found_eclipse = False

    for aura in buff_uptimes:
        name = (aura.get("name") or "").lower()
        total = aura.get("totalUptime", 0)

        if any(kw in name for kw in _ECLIPSE_BUFF_KEYWORDS):
            eclipse_ms += total
            found_eclipse = True
        if any(kw in name for kw in _STARLORD_BUFF_KEYWORDS):
            starlord_ms += total
        if any(kw in name for kw in _CA_BUFF_KEYWORDS):
            ca_ms += total

    if not found_eclipse:
        return None

    def _pct(ms: float) -> float:
        return round(min((ms / fight_dur_ms) * 100.0, 100.0), 1)

    return EclipseMetrics(
        eclipse_uptime_pct=_pct(eclipse_ms),
        avg_eclipse_gap_sec=0.0,
        ca_eclipse_coverage_pct=_pct(ca_ms),
        starlord_uptime_pct=_pct(starlord_ms),
    )


# ============================================================
# Top Issues 归纳
# ============================================================


def summarize_top_issues(
    rotation_gaps: list[SpellGap],
    cooldown_issues: list[CooldownIssue],
    defensive_issues: list[DefensiveIssue],
    build_div: BuildDivergence,
    player_deaths: int,
    downtime: Optional[DowntimeAnalysis] = None,
    cd_window_analysis: Optional[EventLinkingAnalysis] = None,
    talent_usage: Optional[TalentUsageAnalysis] = None,
    cd_throughput: Optional[list[CDWindowThroughput]] = None,
    apl_analysis: Any = None,
) -> list[str]:
    """从各维度差距中提炼 3-5 条最可操作的建议。"""
    issues: list[str] = []

    _add_death_issues(issues, player_deaths)
    _add_downtime_issues(issues, downtime)
    _add_cd_density_issues(issues, cd_window_analysis)
    _add_talent_usage_issues(issues, talent_usage)
    _add_undercast_issues(issues, rotation_gaps)
    _add_defensive_issues(issues, defensive_issues)
    _add_cooldown_issues(issues, cooldown_issues)
    _add_throughput_issues(issues, cd_throughput)
    _add_apl_issues(issues, apl_analysis)
    _add_build_issues(issues, build_div)

    return issues[:5]


def _add_death_issues(issues: list[str], deaths: int) -> None:
    if deaths > 0:
        issues.append(
            f"Died {deaths} time(s) during the fight — "
            f"review defensive timing."
        )


def _add_downtime_issues(
    issues: list[str],
    downtime: Optional[DowntimeAnalysis],
) -> None:
    if not downtime or downtime.verdict not in ("low_activity", "very_low_activity"):
        return
    gap = round(downtime.benchmark_active_time_pct - downtime.active_time_pct, 1)
    severity = "significantly " if downtime.verdict == "very_low_activity" else ""
    issues.append(
        f"Active time {severity}below benchmark: "
        f"{downtime.active_time_pct:.1f}% vs "
        f"{downtime.benchmark_active_time_pct:.1f}% "
        f"({gap:.1f}% gap, {downtime.total_downtime_sec:.1f}s total downtime)."
    )


def _add_cd_density_issues(
    issues: list[str],
    analysis: Optional[EventLinkingAnalysis],
) -> None:
    if not analysis or analysis.verdict != "low_density_burst":
        return
    low_windows = [
        w for w in analysis.cooldown_windows
        if w.density_pct < _CD_DENSITY_THRESHOLD * 100
    ]
    if low_windows:
        w = low_windows[0]
        issues.append(
            f"Low GCD density during '{w.buff_name}' window "
            f"({w.density_pct:.0f}% of max GCDs) — "
            f"fill every GCD during cooldown windows."
        )


def _add_talent_usage_issues(
    issues: list[str],
    usage: Optional[TalentUsageAnalysis],
) -> None:
    if not usage or not usage.unused_talent_spells:
        return
    spells = usage.unused_talent_spells[:2]
    issues.append(
        f"Talent spell(s) never cast: {', '.join(spells)} — "
        f"these abilities are available but unused."
    )


def _add_undercast_issues(
    issues: list[str],
    gaps: list[SpellGap],
) -> None:
    undercast = sorted(
        [g for g in gaps if g.verdict == "undercast"],
        key=lambda g: g.benchmark_median - g.player_casts,
        reverse=True,
    )
    for g in undercast[:2]:
        diff = g.benchmark_median - g.player_casts
        issues.append(
            f"{g.name}: {g.player_casts} casts vs "
            f"{g.benchmark_median:.0f} benchmark median "
            f"({diff:.0f} fewer, below P25)."
        )


def _add_defensive_issues(
    issues: list[str],
    defensive_issues: list[DefensiveIssue],
) -> None:
    unused = [d for d in defensive_issues if d.verdict == "unused"]
    for d in unused[:1]:
        issues.append(
            f"Defensive '{d.name}' not used "
            f"(top players use it {d.benchmark_usage_rate:.0f}% of fights)."
        )


def _add_cooldown_issues(
    issues: list[str],
    cd_issues: list[CooldownIssue],
) -> None:
    sorted_cd = sorted(cd_issues, key=lambda c: c.missed_uses, reverse=True)
    for c in sorted_cd[:1]:
        if c.missed_uses > 0:
            issues.append(
                f"Cooldown '{c.name}': {c.player_casts} uses vs "
                f"{c.benchmark_median_casts:.0f} benchmark — "
                f"~{c.missed_uses} missed uses."
            )


def _add_throughput_issues(
    issues: list[str],
    throughput: Optional[list[CDWindowThroughput]],
) -> None:
    if not throughput:
        return
    weak = [t for t in throughput if t.verdict == "weak"]
    if weak:
        w = weak[0]
        issues.append(
            f"Weak damage during '{w.ability_name}' window #{w.window_index} — "
            f"only {w.damage_done:.0f} damage vs {w.benchmark_median_damage:.0f} expected."
        )


def _add_apl_issues(issues: list[str], apl: Any) -> None:
    if apl is None or not hasattr(apl, "compliance_pct"):
        return
    if apl.compliance_pct < 70:
        issues.append(
            f"APL compliance low ({apl.compliance_pct:.0f}%) — "
            f"{apl.high_severity_count} high-severity violations."
        )


def _add_build_issues(
    issues: list[str],
    build_div: BuildDivergence,
) -> None:
    if build_div.missing_meta_talents:
        count = len(build_div.missing_meta_talents)
        issues.append(
            f"Build differs from meta (match {build_div.similarity_pct:.0f}%) "
            f"— missing {count} popular talent(s)."
        )
