"""
玩家日志对比分析 — 循环、冷却、防御、天赋、CD 输出。

从 analyze_player_log 主模块拆分，负责将玩家数据与基准数据对比，
产生各维度的差距分析结果。

公开接口:
  - compare_rotation(...) -> list[SpellGap]
  - compare_cooldowns(...) -> list[CooldownIssue]
  - compare_defensives(...) -> list[DefensiveIssue]
  - compare_build(...) -> BuildDivergence
  - compare_talent_usage(...) -> TalentUsageAnalysis
  - compare_cd_throughput(...) -> list[CDWindowThroughput]

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.data import (
    get_class_spec_names,
    get_spec_spells,
    get_spell_name,
    get_talent_id_by_spell,
    get_talent_name,
    get_talent_spec,
    get_talent_spell_id,
)
from src.models import (
    BuildDivergence,
    CDWindowThroughput,
    CooldownIssue,
    DefensiveIssue,
    EventLinkingAnalysis,
    SpellGap,
    TalentUsageAnalysis,
    TalentUsageGap,
)

logger = logging.getLogger(__name__)


# ============================================================
# 循环差距
# ============================================================


def compare_rotation(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    fight_duration: float,
    rotation_bench: Any,
) -> list[SpellGap]:
    """将玩家施法数据与基准循环数据对比，产生 SpellGap 列表。"""
    gaps: list[SpellGap] = []
    dur_min = fight_duration / 60.0 if fight_duration > 0 else 1.0

    # 反向名称索引: name → (spell_id, count)
    name_to_player = _build_name_index(player_spell_names, player_spell_counts)

    for spell_stat in rotation_bench.top_spells:
        sid = spell_stat.spell_id
        player_casts = player_spell_counts.get(sid, 0)

        # spell ID 匹配不到时按名称回退
        if player_casts == 0 and spell_stat.name:
            match = name_to_player.get(spell_stat.name.lower())
            if match:
                sid, player_casts = match

        player_cpm = round(player_casts / dur_min, 2)
        percentile, verdict = _classify_spell_gap(
            player_casts, spell_stat.percentiles,
        )

        name = (
            player_spell_names.get(sid)
            or spell_stat.name
            or f"Spell {sid}"
        )
        gaps.append(SpellGap(
            name=name,
            spell_id=sid,
            player_casts=player_casts,
            player_cpm=player_cpm,
            benchmark_median=spell_stat.total_casts,
            benchmark_cpm=spell_stat.cpm,
            percentile=percentile,
            verdict=verdict,
        ))

    return gaps


def _build_name_index(
    spell_names: dict[int, str],
    spell_counts: dict[int, int],
) -> dict[str, tuple[int, int]]:
    """构建 name → (spell_id, count) 反向索引。"""
    index: dict[str, tuple[int, int]] = {}
    for sid, name in spell_names.items():
        count = spell_counts.get(sid, 0)
        lower = name.lower()
        if lower not in index or count > index[lower][1]:
            index[lower] = (sid, count)
    return index


def _classify_spell_gap(
    player_casts: int,
    percentiles: dict[str, float],
) -> tuple[str, str]:
    """根据施法次数确定百分位桶和判定。"""
    p25 = percentiles.get("p25", 0.0)
    p50 = percentiles.get("p50", 0.0)
    p75 = percentiles.get("p75", 0.0)

    if player_casts < p25:
        return "below_p25", "undercast"
    elif player_casts < p50:
        return "p25_p50", "ok"
    elif player_casts < p75:
        return "p50_p75", "ok"
    return "above_p75", "ok"


# ============================================================
# 冷却技能差距
# ============================================================


def compare_cooldowns(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    timeline_bench: Any,
    player_talents: list[dict] | None = None,
    spec: str = "",
) -> list[CooldownIssue]:
    """将玩家冷却技能使用与基准时间线对比。"""
    issues: list[CooldownIssue] = []

    player_talent_sids = _extract_talent_spell_ids(player_talents)
    spec_spell_by_name = _build_spec_spell_index(spec)

    for ability in timeline_bench.abilities:
        median_casts = ability.total_casts.get("median", 0.0)

        matched_sid = _match_ability_spell_id(
            ability.name, player_spell_names,
        )
        if not matched_sid:
            matched_sid = spec_spell_by_name.get(ability.name.lower())

        # 检查天赋条件 — 跳过玩家未选择的天赋技能
        if matched_sid and _should_skip_talent_spell(
            matched_sid, player_talent_sids,
            player_spell_counts, player_spell_names,
            spec_spell_by_name, ability.name,
        ):
            continue

        player_casts = (
            player_spell_counts.get(matched_sid, 0) if matched_sid else 0
        )
        missed = max(0, int(median_casts - player_casts))

        if missed > 0:
            issues.append(CooldownIssue(
                name=ability.name,
                spell_id=matched_sid or 0,
                player_casts=player_casts,
                benchmark_median_casts=median_casts,
                missed_uses=missed,
            ))

    return issues


def _extract_talent_spell_ids(
    talents: list[dict] | None,
) -> set[int]:
    """从天赋列表提取 spell_id 集合。"""
    ids: set[int] = set()
    if not talents:
        return ids
    for t in talents:
        tid = t.get("id") or t.get("talentID")
        if tid:
            sid = get_talent_spell_id(tid)
            if sid:
                ids.add(sid)
    return ids


def _build_spec_spell_index(spec: str) -> dict[str, int]:
    """构建 spec CD 技能名称 → spell_id 映射。"""
    index: dict[str, int] = {}
    if not spec:
        return index
    for spell in get_spec_spells(spec):
        name = spell.get("name", "")
        sid = spell.get("spell_id", 0)
        if name and sid:
            index[name.lower()] = sid
    return index


def _should_skip_talent_spell(
    matched_sid: int,
    player_talent_sids: set[int],
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    spec_spell_by_name: dict[str, int],
    ability_name: str,
) -> bool:
    """判断是否应跳过天赋授予技能的对比。"""
    talent_entry_id = get_talent_id_by_spell(matched_sid)

    if talent_entry_id is not None and player_talent_sids:
        if matched_sid not in player_talent_sids:
            logger.debug(
                "跳过天赋技能对比: %s (spell_id=%d) — 玩家未选择该天赋",
                ability_name, matched_sid,
            )
            return True
    elif talent_entry_id is None:
        if (matched_sid not in player_spell_counts
                and matched_sid in spec_spell_by_name.values()):
            name_lower = ability_name.lower()
            player_has_it = any(
                name_lower in n.lower()
                for n in player_spell_names.values()
            )
            if not player_has_it:
                logger.debug(
                    "跳过未使用的专精技能: %s (spell_id=%d)",
                    ability_name, matched_sid,
                )
                return True
    return False


def _match_ability_spell_id(
    ability_name: str,
    spell_names: dict[int, str],
) -> Optional[int]:
    """按名称反查 spell_id（大小写不敏感）。"""
    lower_name = ability_name.lower()
    for sid, name in spell_names.items():
        if name.lower() == lower_name:
            return sid
    return None


# ============================================================
# 防御技能差距
# ============================================================


def compare_defensives(
    player_spell_counts: dict[int, int],
    player_spell_names: dict[int, str],
    defensive_bench: Any,
) -> list[DefensiveIssue]:
    """将玩家防御技能使用与基准对比。"""
    issues: list[DefensiveIssue] = []

    for timing in defensive_bench.defensive_timings:
        sid = timing.spell_id
        player_casts = player_spell_counts.get(sid, 0)
        player_used = player_casts > 0

        if timing.usage_rate > 50.0 and not player_used:
            verdict = "unused"
        elif timing.usage_rate > 50.0 and player_casts < len(timing.clusters):
            verdict = "underused"
        else:
            verdict = "ok"

        issues.append(DefensiveIssue(
            name=timing.name,
            spell_id=sid,
            player_used=player_used,
            player_cast_count=player_casts,
            benchmark_usage_rate=timing.usage_rate,
            verdict=verdict,
        ))

    return issues


# ============================================================
# 天赋构建差异
# ============================================================


def compare_build(
    player_talents: list[dict],
    build_bench: Any,
    spec: str = "",
) -> BuildDivergence:
    """将玩家天赋与热门构建对比。"""
    if not player_talents or not build_bench.builds:
        return BuildDivergence()

    valid_spec_names = get_class_spec_names(spec) if spec else set()
    player_ids, entry_to_node = _extract_player_talent_ids(player_talents)

    if not player_ids:
        return BuildDivergence()

    best_idx, best_overlap, build_talent_sets = _find_best_build_match(
        player_ids, build_bench.builds,
    )

    best_set = build_talent_sets[best_idx] if build_talent_sets else set()
    missing = best_set - player_ids
    extra = player_ids - best_set

    missing_names = _resolve_talent_names(
        missing, entry_to_node, valid_spec_names,
    )
    extra_names = _resolve_talent_names(
        extra, entry_to_node, valid_spec_names,
    )

    return BuildDivergence(
        best_match_build=best_idx + 1,
        similarity_pct=round(best_overlap * 100, 1),
        missing_meta_talents=missing_names,
        extra_talents=extra_names,
    )


def _extract_player_talent_ids(
    talents: list[dict],
) -> tuple[set[int], dict[int, int]]:
    """提取玩家天赋 ID 集合和 entry→node 映射。"""
    player_ids: set[int] = set()
    entry_to_node: dict[int, int] = {}
    for t in talents:
        tid = t.get("id") or t.get("talentID")
        nid = t.get("nodeID")
        if tid:
            player_ids.add(tid)
            if nid:
                entry_to_node[tid] = nid
    return player_ids, entry_to_node


def _find_best_build_match(
    player_ids: set[int],
    builds: list[Any],
) -> tuple[int, float, list[set[int]]]:
    """在热门构建中找到最佳匹配。"""
    best_idx = 0
    best_overlap = 0.0
    build_talent_sets: list[set[int]] = []

    for i, build in enumerate(builds):
        build_ids: set[int] = set()
        for entry in build.talent_import.split(","):
            parts = entry.strip().split(":")
            if parts and parts[0].isdigit():
                build_ids.add(int(parts[0]))
        build_talent_sets.append(build_ids)

        if build_ids:
            overlap = len(player_ids & build_ids) / len(
                player_ids | build_ids
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i

    return best_idx, best_overlap, build_talent_sets


def _resolve_talent_names(
    talent_ids: set[int],
    entry_to_node: dict[int, int],
    valid_spec_names: set[str],
) -> list[str]:
    """解析天赋名称，过滤跨职业天赋。"""
    names: list[str] = []
    for tid in sorted(talent_ids):
        if valid_spec_names:
            talent_spec = get_talent_spec(tid)
            if talent_spec is not None and talent_spec not in valid_spec_names:
                continue

        lookup = entry_to_node.get(tid, tid)
        zh = get_talent_name(lookup, lang="zh")
        en = get_talent_name(lookup, lang="en")
        if not zh and not en and lookup != tid:
            zh = get_talent_name(tid, lang="zh")
            en = get_talent_name(tid, lang="en")
        if zh and en and zh != en:
            names.append(f"{zh} ({en})")
        else:
            names.append(zh or en or f"TalentID {tid}")
    return names


# ============================================================
# 天赋技能使用分析
# ============================================================


def compare_talent_usage(
    talents: list[dict],
    spell_counts: dict[int, int],
    spell_names: dict[int, str],
    fight_duration: float,
    rotation_bench: Any,
) -> TalentUsageAnalysis:
    """检查玩家天赋授予的技能是否被使用。"""
    dur_min = fight_duration / 60.0 if fight_duration > 0 else 1.0
    gaps: list[TalentUsageGap] = []
    unused_spells: list[str] = []

    bench_by_id = _build_bench_lookup(rotation_bench)
    seen_spell_ids: set[int] = set()

    for t in talents:
        gap = _evaluate_talent_usage(
            t, spell_counts, spell_names, dur_min,
            bench_by_id, seen_spell_ids,
        )
        if gap is None:
            continue
        talent_gap, spell_name, verdict = gap
        if verdict == "unused":
            unused_spells.append(spell_name)
        gaps.append(talent_gap)

    return TalentUsageAnalysis(
        talent_gaps=gaps,
        unused_talent_spells=unused_spells,
    )


def _build_bench_lookup(rotation_bench: Any) -> dict[int, Any]:
    """构建基准查找表: spell_id → SpellStats。"""
    lookup: dict[int, Any] = {}
    if rotation_bench is not None and hasattr(rotation_bench, "top_spells"):
        for ss in rotation_bench.top_spells:
            lookup[ss.spell_id] = ss
    return lookup


def _evaluate_talent_usage(
    talent: dict,
    spell_counts: dict[int, int],
    spell_names: dict[int, str],
    dur_min: float,
    bench_by_id: dict[int, Any],
    seen: set[int],
) -> Optional[tuple[TalentUsageGap, str, str]]:
    """评估单个天赋技能的使用情况，返回 (gap, spell_name, verdict) 或 None。"""
    tid = talent.get("id") or talent.get("talentID")
    if not tid:
        return None

    spell_id = get_talent_spell_id(tid)
    if not spell_id or spell_id in seen:
        return None
    seen.add(spell_id)

    talent_name = _format_talent_name(tid)
    spell_name = (
        spell_names.get(spell_id)
        or get_spell_name(spell_id)
        or f"Spell {spell_id}"
    )

    player_casts = spell_counts.get(spell_id, 0)
    player_cpm = round(player_casts / dur_min, 2)

    bench = bench_by_id.get(spell_id)
    bench_median = bench.total_casts if bench else 0.0
    bench_cpm = bench.cpm if bench else 0.0

    if player_casts == 0 and bench_median > 0:
        verdict = "unused"
    elif bench_median > 0 and player_casts < bench_median * 0.5:
        verdict = "underused"
    else:
        verdict = "ok"

    if bench_median <= 0 and verdict == "ok":
        return None

    gap = TalentUsageGap(
        talent_name=talent_name,
        talent_id=tid,
        spell_name=spell_name,
        spell_id=spell_id,
        player_casts=player_casts,
        benchmark_median_casts=bench_median,
        player_cpm=player_cpm,
        benchmark_cpm=bench_cpm,
        verdict=verdict,
    )
    return gap, spell_name, verdict


def _format_talent_name(tid: int) -> str:
    """格式化天赋名称（中英双语）。"""
    zh = get_talent_name(tid, lang="zh")
    en = get_talent_name(tid, lang="en")
    if zh and en and zh != en:
        return f"{zh} ({en})"
    return zh or en or f"TalentID {tid}"


# ============================================================
# CD 窗口输出分析
# ============================================================


def compare_cd_throughput(
    cd_window_analysis: Optional[EventLinkingAnalysis],
    damage_events: list[dict],
    fight_start_time: int,
    rotation_bench: Any,
) -> list[CDWindowThroughput]:
    """分析每个 CD 窗口期间的伤害输出。"""
    if not cd_window_analysis or not cd_window_analysis.cooldown_windows:
        return []

    bench_dps = 0.0
    if rotation_bench is not None and hasattr(rotation_bench, "dps_median"):
        bench_dps = rotation_bench.dps_median

    results: list[CDWindowThroughput] = []
    name_counters: dict[str, int] = defaultdict(int)

    for window in cd_window_analysis.cooldown_windows:
        name_counters[window.buff_name] += 1
        window_index = name_counters[window.buff_name]

        damage_done = _sum_window_damage(
            damage_events, fight_start_time,
            window.start_sec, window.end_sec,
        )
        benchmark_damage = (
            bench_dps * window.duration_sec if bench_dps > 0 else 0.0
        )
        verdict = _classify_throughput(damage_done, benchmark_damage)

        results.append(CDWindowThroughput(
            ability_name=window.buff_name,
            window_index=window_index,
            damage_done=round(damage_done, 1),
            casts_during=window.casts_during,
            active_time_pct=window.density_pct,
            benchmark_median_damage=round(benchmark_damage, 1),
            verdict=verdict,
        ))

    return results


def _sum_window_damage(
    events: list[dict],
    fight_start_time: int,
    start_sec: float,
    end_sec: float,
) -> float:
    """统计窗口内伤害总量。"""
    window_start_ms = fight_start_time + int(start_sec * 1000)
    window_end_ms = fight_start_time + int(end_sec * 1000)
    total = 0.0
    for evt in events:
        ts = evt.get("timestamp", 0)
        if window_start_ms <= ts <= window_end_ms:
            total += evt.get("amount", 0) + evt.get("absorbed", 0)
    return total


def _classify_throughput(
    damage_done: float,
    benchmark_damage: float,
) -> str:
    """根据伤害量判定窗口输出等级。"""
    if benchmark_damage <= 0:
        return "ok"
    ratio = damage_done / benchmark_damage
    if ratio >= 1.0:
        return "strong"
    elif ratio >= 0.5:
        return "average"
    return "weak"
