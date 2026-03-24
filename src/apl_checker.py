"""
APL 循环合规性检查器 — 对比玩家施法序列与 SimC APL 规则。

v1 实现: 简化的 APL 条件评估，基于 buff 窗口和施法历史。
跳过资源条件（无可用数据）。

公开接口:
  - check_player_apl(spec, cast_timestamps, spell_names, buff_uptimes, ...)
    -> APLAnalysis | None

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from collections import Counter, defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.data import get_spec_apl, get_spec_spells, get_spell_name
from src.models import APLAnalysis, APLViolation

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
# SimC spell name → WCL spell name 映射（v1 手动维护）
_SPELL_NAME_ALIASES: dict[str, list[str]] = {
    "starsurge": ["starsurge", "星涌术"],
    "starfire": ["starfire", "星火术"],
    "wrath": ["wrath", "愤怒"],
    "moonfire": ["moonfire", "月火术"],
    "sunfire": ["sunfire", "阳炎术"],
    "starfall": ["starfall", "星辰坠落"],
    "celestial_alignment": ["celestial alignment", "天体校准"],
    "incarnation": ["incarnation: chosen of elune", "化身：艾露恩之眷"],
    "convoke_the_spirits": ["convoke the spirits", "百灵之召"],
    "force_of_nature": ["force of nature", "自然之力"],
    "fury_of_elune": ["fury of elune", "艾露恩之怒"],
    "warrior_of_elune": ["warrior of elune", "艾露恩的战士"],
    "wild_mushroom": ["wild mushroom", "野性蘑菇"],
    "new_moon": ["new moon", "新月"],
    "half_moon": ["half moon", "半月"],
    "full_moon": ["full moon", "满月"],
}

# 条件中已知的资源/不可评估条件前缀
_SKIP_CONDITION_PREFIXES = (
    "astral_power", "eclipse", "ap", "resource",
    "target.health", "raid_event", "fight_remains",
    "variable", "active_enemies", "spell_targets",
)


# ============================================================
# 技能名称匹配
# ============================================================


def _normalize_spell_name(name: str) -> str:
    """将技能名称标准化为小写、下划线格式。"""
    return name.lower().replace(" ", "_").replace(":", "").replace("'", "")


def _match_simc_to_wcl(
    simc_spell: str,
    spell_names: dict[int, str],
) -> Optional[int]:
    """
    将 SimC 技能名称匹配到 WCL spell_id。

    先尝试别名表，再尝试模糊匹配。
    """
    simc_lower = simc_spell.lower().replace("_", " ")

    # 通过别名表匹配
    aliases = _SPELL_NAME_ALIASES.get(simc_spell.lower(), [simc_lower])

    for sid, name in spell_names.items():
        name_lower = name.lower()
        for alias in aliases:
            if alias in name_lower or name_lower.startswith(alias):
                return sid

    return None


# ============================================================
# 条件评估（简化版）
# ============================================================


def _evaluate_condition(
    condition: str,
    buff_state: dict[int, bool],
    last_cast_times: dict[int, float],
    current_time: float,
    talent_spell_ids: set[int],
) -> Optional[bool]:
    """
    简化条件评估。

    返回 True/False 表示条件满足/不满足，
    None 表示条件无法评估（资源等）。
    """
    cond = condition.strip().lower()

    # 跳过不可评估条件
    for prefix in _SKIP_CONDITION_PREFIXES:
        if cond.startswith(prefix):
            return None

    # buff.X.up / buff.X.down
    if cond.startswith("buff.") and (".up" in cond or ".down" in cond):
        # 简化: 返回 None（需要实际 buff 追踪）
        return None

    # cooldown.X.ready
    if "cooldown." in cond and ".ready" in cond:
        return None

    # talent.X.enabled
    if cond.startswith("talent.") and ".enabled" in cond:
        return None  # 天赋总是已启用（玩家装了就有）

    # 无法评估
    return None


# ============================================================
# APL 检查主逻辑
# ============================================================


def _load_apl_rules(spec: str) -> Optional[list[dict]]:
    """加载 APL 规则，仅加载 default 和 cooldowns 列表。"""
    apl_data = get_spec_apl(spec)
    if not apl_data:
        return None

    rules = apl_data.get("rules", [])
    if not rules:
        return None

    # v1: 排除 precombat（战前预施法），保留所有战斗中的 action list
    filtered = [
        r for r in rules
        if r.get("action_list") != "precombat"
    ]
    return filtered if filtered else rules


def _build_apl_priority_list(
    rules: list[dict],
    simc_to_wcl: dict[str, int],
) -> list[tuple[str, int, int]]:
    """
    构建 APL 技能优先级列表（排除 DoT/维护技能），去重保留最高优先级。
    """
    # DoT/维护类和 Eclipse 进入技能 — 含 target_if，无法评估优先级
    _DOT_SPELLS = {"moonfire", "sunfire", "stellar_flare",
                    "solar_eclipse", "lunar_eclipse"}
    dot_spell_ids: set[int] = {
        simc_to_wcl[s] for s in _DOT_SPELLS if s in simc_to_wcl
    }

    priority_list: list[tuple[str, int, int]] = []
    for rule in rules:
        simc_spell = rule.get("spell", "")
        wcl_id = simc_to_wcl.get(simc_spell)
        if wcl_id and wcl_id not in dot_spell_ids:
            if not any(s == simc_spell for s, _, _ in priority_list):
                priority_list.append((
                    simc_spell, wcl_id, rule.get("priority", 999)
                ))
    return priority_list


def _build_cd_durations(spec: str) -> dict[int, float]:
    """构建 CD 时长映射: WCL spell_id -> CD 时长（秒）。"""
    cd_durations: dict[int, float] = {}
    for spell_info in get_spec_spells(spec):
        cd_sec = spell_info.get("cooldown", 0)
        sid = spell_info.get("spell_id")
        if cd_sec and sid:
            cd_durations[sid] = float(cd_sec)
    return cd_durations


def _build_spell_mappings(
    rules: list[dict],
    spell_names: dict[int, str],
    spec: str,
    talents: list[dict],
) -> Optional[dict[str, Any]]:
    """
    构建 SimC<->WCL 技能映射、优先级列表和 CD 信息。

    返回包含所有映射数据的字典，映射为空时返回 None。
    """
    # SimC spell -> WCL spell_id 正向映射
    simc_to_wcl: dict[str, int] = {}
    for rule in rules:
        simc_spell = rule.get("spell", "")
        if simc_spell and simc_spell not in simc_to_wcl:
            wcl_id = _match_simc_to_wcl(simc_spell, spell_names)
            if wcl_id:
                simc_to_wcl[simc_spell] = wcl_id

    if not simc_to_wcl:
        return None

    # 天赋 spell_id 集合
    talent_spell_ids: set[int] = set()
    for t in talents:
        from src.data import get_talent_spell_id
        tid = t.get("id") or t.get("talentID")
        if tid:
            sid = get_talent_spell_id(tid)
            if sid:
                talent_spell_ids.add(sid)

    return {
        "simc_to_wcl": simc_to_wcl,
        "wcl_to_simc": {v: k for k, v in simc_to_wcl.items()},
        "talent_spell_ids": talent_spell_ids,
        "apl_spell_priority": _build_apl_priority_list(rules, simc_to_wcl),
        "cd_durations": _build_cd_durations(spec),
    }


def _detect_apl_violations(
    sorted_casts: list[tuple[int, int]],
    fight_start_time: int,
    wcl_to_simc: dict[int, str],
    apl_spell_priority: list[tuple[str, int, int]],
    cd_durations: dict[int, float],
) -> tuple[list[APLViolation], Counter]:
    """
    遍历施法序列，检测 APL 优先级违规。

    返回 (violations, violation_patterns)。
    """
    cd_available_at: dict[int, float] = {}
    violations: list[APLViolation] = []
    violation_patterns: Counter = Counter()

    for ts_ms, spell_id in sorted_casts:
        current_sec = (ts_ms - fight_start_time) / 1000.0

        # 更新该技能的 CD 可用时间
        cd_dur = cd_durations.get(spell_id)
        if cd_dur:
            cd_available_at[spell_id] = current_sec + cd_dur

        # 非追踪技能，跳过
        actual_simc = wcl_to_simc.get(spell_id)
        if not actual_simc:
            continue

        # 查找该技能在 APL 中的优先级
        actual_priority = 999
        for simc_spell, wcl_id, prio in apl_spell_priority:
            if wcl_id == spell_id:
                actual_priority = prio
                break

        # 检查是否有更高优先级的技能被跳过
        violation = _find_priority_violation(
            spell_id, actual_simc, actual_priority,
            current_sec, apl_spell_priority, cd_available_at,
        )
        if violation:
            violation_patterns[f"{violation.expected_spell} > {actual_simc}"] += 1
            violations.append(violation)

    return violations, violation_patterns


def _find_priority_violation(
    spell_id: int,
    actual_simc: str,
    actual_priority: int,
    current_sec: float,
    apl_spell_priority: list[tuple[str, int, int]],
    cd_available_at: dict[int, float],
) -> Optional[APLViolation]:
    """检查单次施法是否跳过了更高优先级技能，返回最高优先级违规。"""
    for simc_spell, wcl_id, prio in apl_spell_priority:
        if prio >= actual_priority:
            break
        if wcl_id == spell_id:
            continue
        if cd_available_at.get(wcl_id, 0) > current_sec:
            continue

        severity = "medium" if prio < actual_priority - 5 else "low"
        if prio <= 3:
            severity = "high"

        return APLViolation(
            timestamp_sec=round(current_sec, 2),
            expected_spell=simc_spell,
            actual_spell=actual_simc,
            rule_priority=prio,
            severity=severity,
            benchmark_weight=0.0,
        )
    return None


def _compute_compliance(
    spec: str,
    sorted_casts: list[tuple[int, int]],
    wcl_to_simc: dict[int, str],
    violations: list[APLViolation],
    violation_patterns: Counter,
) -> APLAnalysis:
    """计算合规率并组装最终分析结果。"""
    total_tracked = sum(1 for _, sid in sorted_casts if sid in wcl_to_simc)
    compliance_pct = (
        round((1 - len(violations) / total_tracked) * 100, 1)
        if total_tracked > 0 else 100.0
    )
    compliance_pct = max(0.0, compliance_pct)

    apl_data = get_spec_apl(spec)
    version = apl_data.get("version", "unknown") if apl_data else "unknown"

    return APLAnalysis(
        spec=spec,
        apl_version=version,
        compliance_pct=compliance_pct,
        violations=violations[:50],
        high_severity_count=sum(1 for v in violations if v.severity == "high"),
        top_violation_patterns=[
            f"{p} ({c}x)" for p, c in violation_patterns.most_common(5)
        ],
    )


# ============================================================
# APL 检查入口 — 编排器
# ============================================================


def check_player_apl(
    spec: str,
    cast_timestamps: list[tuple[int, int]],
    spell_names: dict[int, str],
    buff_uptimes: list[dict],
    fight_start_time: int,
    fight_duration: float,
    talents: list[dict],
) -> Optional[APLAnalysis]:
    """
    检查玩家施法序列是否符合 APL 优先级。

    对每个施法事件，检查是否有更高优先级的 APL 规则被跳过。
    """
    rules = _load_apl_rules(spec)
    if not rules:
        return None

    mappings = _build_spell_mappings(rules, spell_names, spec, talents)
    if not mappings:
        return None

    sorted_casts = sorted(cast_timestamps, key=lambda x: x[0])

    violations, patterns = _detect_apl_violations(
        sorted_casts, fight_start_time,
        mappings["wcl_to_simc"], mappings["apl_spell_priority"],
        mappings["cd_durations"],
    )

    return _compute_compliance(
        spec, sorted_casts, mappings["wcl_to_simc"],
        violations, patterns,
    )
