"""
get_spec_info 工具 — 查询职业/专精/技能静态数据。

纯本地数据服务，不调用 WCL API。
数据来源: src/data/ 下的 JSON 文件（由 export_lorrgs_data.py 生成）。

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
from src.data import get_all_classes, get_all_specs, get_spec

logger = logging.getLogger(__name__)

# ============================================================
# 技能分类标签映射
# ============================================================
_TAG_CATEGORIES: dict[str, str] = {
    "dps": "offensive",
    "damage": "offensive",
    "defensive": "defensive",
    "tank": "defensive",
    "raid_cd": "utility",
    "move": "utility",
    "dynamic_cd": "offensive",
}


# ============================================================
# 内部辅助
# ============================================================
def _categorize_spell(spell: dict) -> str:
    """根据 tags 判定技能类别。"""
    tags = spell.get("tags", [])
    for tag in tags:
        if tag in _TAG_CATEGORIES:
            return _TAG_CATEGORIES[tag]
    # 按 event_type 兜底
    event_type = spell.get("event_type", "cast")
    if "buff" in event_type:
        return "buff"
    if "debuff" in event_type:
        return "offensive"
    return "offensive"


def _build_full_spec_response(spec_data: dict, include_spells: bool) -> dict:
    """构建单个专精的完整响应。"""
    result: dict[str, Any] = {
        "slug": spec_data["slug"],
        "name": spec_data["name"],
        "full_name": spec_data["full_name"],
        "class_name": spec_data["class_name"],
        "role": spec_data["role"],
        "spell_count": len(spec_data.get("spells", [])),
    }
    if include_spells:
        categorized: dict[str, list[dict]] = {
            "offensive": [],
            "defensive": [],
            "utility": [],
            "buff": [],
        }
        for spell in spec_data.get("spells", []):
            cat = _categorize_spell(spell)
            categorized.setdefault(cat, []).append(spell)
        result["spells_by_category"] = categorized
    return result


def _build_specs_summary() -> list[dict]:
    """构建所有专精的简洁概览列表。"""
    summaries: list[dict] = []
    for spec in get_all_specs():
        summaries.append({
            "slug": spec["slug"],
            "full_name": spec["full_name"],
            "class_name": spec["class_name"],
            "role": spec["role"],
            "spell_count": len(spec.get("spells", [])),
        })
    return summaries


# ============================================================
# 公开接口
# ============================================================
async def get_spec_info(
    spec: Optional[str] = None,
    include_spells: bool = True,
) -> dict:
    """
    查询职业/专精静态数据。

    Args:
        spec: 专精 slug（如 "frost-death-knight"），省略则返回全部概览
        include_spells: 是否包含技能详情（仅指定 spec 时有效）

    Returns:
        指定 spec 时: 完整专精数据（含分类技能）
        省略 spec 时: 所有专精的简洁列表
    """
    if spec:
        spec_data = get_spec(spec)
        if spec_data is None:
            logger.warning("未找到专精: %s", spec)
            return {
                "error": f"未找到专精: {spec}",
                "available_specs": [s["slug"] for s in get_all_specs()],
            }
        return _build_full_spec_response(spec_data, include_spells)

    # 未指定 spec — 返回全部概览
    return {
        "classes": get_all_classes(),
        "specs": _build_specs_summary(),
    }
