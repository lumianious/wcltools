"""
get_top_builds 工具 — 聚合顶尖玩家的天赋/装备/属性构建。

从 WCL characterRankings(includeCombatantInfo: true) 获取数据，
解析天赋构建、饰品使用率、属性分布。

WCL 返回结构:
  rankings[].talents = [{talentID, points}, ...]
  rankings[].gear = [item0..item17]  # index 12/13 = 饰品
  rankings[].bracketData = ilvl

缓存 6 小时。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
import statistics
from collections import Counter
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.data import get_spell_name, get_talent_name, get_talent_tree
from src.models import (
    FlexNode,
    StatDistribution,
    StatProfile,
    TalentBuild,
    TopBuildsResponse,
    TrinketInfo,
)
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时

# WCL 难度 ID 映射
DIFFICULTY_MAP: dict[str, int] = {
    "normal": 3,
    "heroic": 4,
    "mythic": 5,
    "mythic_plus": 10,  # M+ / Challenge Mode
}

# 饰品在 gear 数组中的索引（0-based）
TRINKET_SLOT_INDICES = (12, 13)

# WCL spec 名称需要拆分为 className + specName
SPEC_MAPPING: dict[str, tuple[str, str]] = {
    # 死亡骑士
    "blood-death-knight": ("DeathKnight", "Blood"),
    "frost-death-knight": ("DeathKnight", "Frost"),
    "unholy-death-knight": ("DeathKnight", "Unholy"),
    # 恶魔猎手
    "havoc-demon-hunter": ("DemonHunter", "Havoc"),
    "vengeance-demon-hunter": ("DemonHunter", "Vengeance"),
    # 德鲁伊
    "balance-druid": ("Druid", "Balance"),
    "feral-druid": ("Druid", "Feral"),
    "guardian-druid": ("Druid", "Guardian"),
    "restoration-druid": ("Druid", "Restoration"),
    # 唤魔师
    "devastation-evoker": ("Evoker", "Devastation"),
    "preservation-evoker": ("Evoker", "Preservation"),
    "augmentation-evoker": ("Evoker", "Augmentation"),
    # 猎人
    "beast-mastery-hunter": ("Hunter", "BeastMastery"),
    "marksmanship-hunter": ("Hunter", "Marksmanship"),
    "survival-hunter": ("Hunter", "Survival"),
    # 法师
    "arcane-mage": ("Mage", "Arcane"),
    "fire-mage": ("Mage", "Fire"),
    "frost-mage": ("Mage", "Frost"),
    # 武僧
    "brewmaster-monk": ("Monk", "Brewmaster"),
    "mistweaver-monk": ("Monk", "Mistweaver"),
    "windwalker-monk": ("Monk", "Windwalker"),
    # 圣骑士
    "holy-paladin": ("Paladin", "Holy"),
    "protection-paladin": ("Paladin", "Protection"),
    "retribution-paladin": ("Paladin", "Retribution"),
    # 牧师
    "discipline-priest": ("Priest", "Discipline"),
    "holy-priest": ("Priest", "Holy"),
    "shadow-priest": ("Priest", "Shadow"),
    # 潜行者
    "assassination-rogue": ("Rogue", "Assassination"),
    "outlaw-rogue": ("Rogue", "Outlaw"),
    "subtlety-rogue": ("Rogue", "Subtlety"),
    # 萨满
    "elemental-shaman": ("Shaman", "Elemental"),
    "enhancement-shaman": ("Shaman", "Enhancement"),
    "restoration-shaman": ("Shaman", "Restoration"),
    # 术士
    "affliction-warlock": ("Warlock", "Affliction"),
    "demonology-warlock": ("Warlock", "Demonology"),
    "destruction-warlock": ("Warlock", "Destruction"),
    # 战士
    "arms-warrior": ("Warrior", "Arms"),
    "fury-warrior": ("Warrior", "Fury"),
    "protection-warrior": ("Warrior", "Protection"),
}


# ============================================================
# Spec 解析
# ============================================================


def _parse_spec(spec: str) -> tuple[str, str]:
    """
    将 spec slug 解析为 (className, specName)。

    支持 slug 格式: "frost-death-knight"
    """
    key = spec.lower().strip()
    if key in SPEC_MAPPING:
        return SPEC_MAPPING[key]

    # 尝试简单拆分
    parts = key.replace("-", " ").split()
    if len(parts) >= 2:
        spec_name = parts[0].capitalize()
        class_name = "".join(p.capitalize() for p in parts[1:])
        return class_name, spec_name

    raise ValueError(
        f"无法解析 spec: '{spec}'。"
        f"请使用格式如 'frost-death-knight'"
    )


# ============================================================
# WCL 查询
# ============================================================


async def _query_rankings(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    difficulty: int,
) -> dict[str, Any]:
    """查询 WCL 排行榜数据（含战斗信息）。"""
    metric = "dps"
    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "{class_name}"
                    specName: "{spec_name}"
                    metric: {metric}
                    difficulty: {difficulty}
                    includeCombatantInfo: true
                )
            }}
        }}
    """
    data = await client.query(gql)
    return data.get("worldData", {}).get("encounter", {})


# ============================================================
# 天赋构建提取
# ============================================================


def _talent_key(talents: list[dict]) -> str:
    """
    将天赋列表转换为可比较的字符串 key。

    WCL 返回: [{talentID: 96161, points: 2}, ...]
    排序后拼接为 "96161:2,96165:1,..."
    """
    if not talents:
        return ""
    sorted_t = sorted(talents, key=lambda t: t.get("talentID", 0))
    return ",".join(
        f"{t['talentID']}:{t['points']}" for t in sorted_t
    )


def _bilingual_talent_name(tid: int) -> Optional[str]:
    """
    返回双语天赋名称，格式: "中文名 (English Name)"。

    当仅有一种语言时退化为单语。
    """
    zh = get_talent_name(tid, lang="zh")
    en = get_talent_name(tid, lang="en")
    if zh and en and zh != en:
        return f"{zh} ({en})"
    return zh or en


def _build_talent_summary(talents: list[dict]) -> str:
    """
    为天赋列表生成双语名称摘要。

    格式: "中文名 (English Name) / ..." — 用 " / " 连接。
    """
    names: list[str] = []
    for t in talents:
        tid = t.get("talentID", 0)
        name = _bilingual_talent_name(tid)
        if name:
            names.append(name)
    return " / ".join(names) if names else ""


def _extract_talent_builds(
    rankings: list[dict],
) -> tuple[list[TalentBuild], list[FlexNode]]:
    """
    从排行榜数据提取天赋构建和弹性节点。

    WCL rankings[].talents = [{talentID, points}, ...]
    """
    total = len(rankings)
    if total == 0:
        return [], []

    # 统计天赋组合出现次数
    talent_counter: Counter[str] = Counter()
    # 记录每个 key 对应的原始天赋数据（取第一个）
    key_to_talents: dict[str, list[dict]] = {}

    for r in rankings:
        talents = r.get("talents") or []
        key = _talent_key(talents)
        if key:
            talent_counter[key] += 1
            if key not in key_to_talents:
                key_to_talents[key] = talents

    # 取 Top 3 构建
    builds: list[TalentBuild] = []
    for key, count in talent_counter.most_common(3):
        summary = _build_talent_summary(key_to_talents.get(key, []))
        builds.append(
            TalentBuild(
                talent_import=key,
                talent_summary=summary,
                usage_pct=round(count / total * 100, 1),
                player_count=count,
            )
        )

    # 弹性节点: 分析天赋 ID 的分歧
    flex_nodes = _compute_flex_nodes(
        talent_counter, key_to_talents, total
    )
    return builds, flex_nodes


def _compute_flex_nodes(
    talent_counter: Counter[str],
    key_to_talents: dict[str, list[dict]],
    total: int,
) -> list[FlexNode]:
    """
    计算弹性节点 — 不同构建之间的天赋差异。

    比较最流行的两个构建，找出不同的 talentID。
    """
    top_keys = [k for k, _ in talent_counter.most_common(2)]
    if len(top_keys) < 2:
        return []

    # 提取两个构建的 talentID 集合
    set_a = {t["talentID"] for t in key_to_talents[top_keys[0]]}
    set_b = {t["talentID"] for t in key_to_talents[top_keys[1]]}

    # 差异 = 对称差集
    diff_ids = set_a.symmetric_difference(set_b)
    if not diff_ids:
        return []

    # 统计每个差异 talent 的选取率
    nodes: list[FlexNode] = []
    for tid in sorted(diff_ids):
        pick_count = sum(
            1 for r_key, cnt in talent_counter.items()
            if any(
                t["talentID"] == tid
                for t in key_to_talents.get(r_key, [])
            )
            for _ in range(cnt)
        )
        pct = round(pick_count / total * 100, 1)
        # 尝试解析天赋名称: 双语 → spell 名称 → 兜底
        resolved_name = (
            _bilingual_talent_name(tid)
            or get_spell_name(tid)
            or f"TalentID {tid}"
        )
        nodes.append(
            FlexNode(
                talent_name=resolved_name,
                tree=get_talent_tree(tid) or "",
                pick_rate=pct,
            )
        )

    return sorted(nodes, key=lambda n: n.pick_rate, reverse=True)[:5]


# ============================================================
# 饰品提取
# ============================================================


def _extract_trinkets(rankings: list[dict]) -> list[TrinketInfo]:
    """
    从排行榜数据提取饰品使用率。

    WCL gear 数组: index 12 和 13 为饰品栏。
    """
    total = len(rankings)
    if total == 0:
        return []

    trinket_counter: Counter[tuple[str, int]] = Counter()
    for r in rankings:
        gear = r.get("gear") or []
        for idx in TRINKET_SLOT_INDICES:
            if idx < len(gear):
                item = gear[idx]
                name = item.get("name", f"Item {item.get('id', 0)}")
                item_id = item.get("id", 0)
                if item_id > 0:
                    trinket_counter[(name, item_id)] += 1

    trinkets: list[TrinketInfo] = []
    for (name, item_id), count in trinket_counter.most_common(10):
        trinkets.append(
            TrinketInfo(
                name=name,
                item_id=item_id,
                usage_pct=round(count / total * 100, 1),
                count=count,
            )
        )
    return trinkets


# ============================================================
# 属性分布提取
# ============================================================


def _extract_stat_profile(rankings: list[dict]) -> StatProfile:
    """
    从排行榜数据提取装等分布。

    WCL rankings[].bracketData = ilvl (整数)
    注意: characterRankings 不返回详细 stats，只有 bracketData 作为 ilvl。
    """
    if not rankings:
        return StatProfile()

    ilvls: list[float] = []
    for r in rankings:
        ilvl = r.get("bracketData")
        if ilvl and ilvl > 0:
            ilvls.append(float(ilvl))

    return StatProfile(
        item_level=_compute_distribution(ilvls),
    )


def _compute_distribution(values: list[float]) -> StatDistribution:
    """计算中位数 / P25 / P75 分布。"""
    if not values:
        return StatDistribution()
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    return StatDistribution(
        median=round(statistics.median(sorted_vals), 1),
        p25=round(sorted_vals[max(0, n // 4 - 1)], 1),
        p75=round(sorted_vals[min(n - 1, 3 * n // 4)], 1),
    )


# ============================================================
# 公开接口
# ============================================================


async def get_top_builds(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: Optional[str] = "heroic",
) -> TopBuildsResponse:
    """
    获取指定专精在指定 Boss 上的顶尖构建聚合数据。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "frost-death-knight"
        encounter_id: Boss 遭遇 ID
        difficulty: 难度 — "normal" / "heroic" / "mythic"

    Returns:
        TopBuildsResponse 包含天赋、饰品、属性分布
    """
    difficulty = difficulty or "heroic"
    cache_key = f"builds:{spec}:{encounter_id}:{difficulty}"

    # 尝试缓存
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_top_builds 缓存命中")
        return TopBuildsResponse(**cached)

    # 解析 spec
    class_name, spec_name = _parse_spec(spec)
    diff_id = DIFFICULTY_MAP.get(difficulty, 4)

    # 查询 WCL
    encounter_data = await _query_rankings(
        client, encounter_id, class_name, spec_name, diff_id
    )
    encounter_name = encounter_data.get("name", "")
    rankings_data = encounter_data.get("characterRankings", {})

    # characterRankings 返回:
    # { page, hasMorePages, count, rankings: [...] }
    rankings = rankings_data.get("rankings", [])
    sample_size = len(rankings)

    logger.info(
        "get_top_builds: %s on %s (%s), %d 条数据",
        spec, encounter_name, difficulty, sample_size,
    )

    # 提取数据
    builds, flex_nodes = _extract_talent_builds(rankings)
    trinkets = _extract_trinkets(rankings)
    stat_profile = _extract_stat_profile(rankings)

    response = TopBuildsResponse(
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        difficulty=difficulty,
        sample_size=sample_size,
        builds=builds,
        flex_nodes=flex_nodes,
        top_trinkets=trinkets,
        stat_profile=stat_profile,
    )

    # 写入缓存
    cache_set(cache_key, response.model_dump())
    return response
