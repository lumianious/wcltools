"""
WoW 静态数据加载器 — 从 JSON 文件加载职业/专精/技能/Boss/天赋 数据。

数据来源:
  - specs.json / bosses.json: scripts/export_lorrgs_data.py 从 Lorrgs 代码库导出
  - talents.json: scripts/export_talent_data.py 从 Blizzard Game Data API 导出

公开接口:
  - get_all_classes()        → 所有职业列表
  - get_all_specs()          → 所有专精列表
  - get_spec(slug)           → 按 slug 查找单个专精
  - get_spell_name(id)       → 按 spell_id 反查技能名称
  - get_spec_spells(slug)    → 获取指定专精的所有技能
  - get_boss(boss_id)        → 按 ID 查找 Boss
  - get_talent_name(id,lang) → 按天赋节点/条目 ID 查找天赋名称
  - get_talent_tree(id)      → 按天赋节点/条目 ID 查找所属子树 (class/spec/hero)
  - get_talent_spell_id(id)  → 按天赋节点/条目 ID 查找 spell_id

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import json
from pathlib import Path
from typing import Optional

# ============================================================
# 数据目录
# ============================================================
_DATA_DIR = Path(__file__).resolve().parent

# ============================================================
# 延迟加载容器
# ============================================================
_specs_data: dict | None = None
_bosses_data: dict | None = None
_talents_data: dict | None = None
_spell_index: dict[int, str] | None = None
_spec_index: dict[str, dict] | None = None
_boss_index: dict[int, dict] | None = None
_talent_index: dict[str, dict] | None = None


# ============================================================
# 内部加载函数
# ============================================================
def _load_specs() -> dict:
    """加载并缓存 specs.json。"""
    global _specs_data
    if _specs_data is None:
        path = _DATA_DIR / "specs.json"
        with open(path, "r", encoding="utf-8") as f:
            _specs_data = json.load(f)
    return _specs_data


def _load_bosses() -> dict:
    """加载并缓存 bosses.json。"""
    global _bosses_data
    if _bosses_data is None:
        path = _DATA_DIR / "bosses.json"
        with open(path, "r", encoding="utf-8") as f:
            _bosses_data = json.load(f)
    return _bosses_data


def _build_spell_index() -> dict[int, str]:
    """构建 spell_id → name 反查索引（specs + talents + 补充映射）。"""
    global _spell_index
    if _spell_index is None:
        _spell_index = {}

        # 来源 1: specs.json（CD 技能）
        data = _load_specs()
        for spec in data.get("specs", []):
            for spell in spec.get("spells", []):
                sid = spell.get("spell_id")
                name = spell.get("name")
                if sid and name and sid not in _spell_index:
                    _spell_index[sid] = name

        # 来源 2: talents.json（天赋关联技能，含基础旋转技能）
        talent_data = _load_talents()
        for entry in talent_data.get("talents", {}).values():
            sid = entry.get("spell_id")
            # 优先使用双语格式，退化为单语
            name_zh = entry.get("name_zh", "")
            name_en = entry.get("name_en", "")
            if sid and sid not in _spell_index:
                if name_zh and name_en and name_zh != name_en:
                    _spell_index[sid] = f"{name_zh} ({name_en})"
                elif name_zh:
                    _spell_index[sid] = name_zh
                elif name_en:
                    _spell_index[sid] = name_en
    return _spell_index


def _build_spec_index() -> dict[str, dict]:
    """构建 slug → spec 快速查找索引。"""
    global _spec_index
    if _spec_index is None:
        data = _load_specs()
        _spec_index = {
            spec["slug"]: spec
            for spec in data.get("specs", [])
        }
    return _spec_index


def _build_boss_index() -> dict[int, dict]:
    """构建 boss_id → boss 快速查找索引。"""
    global _boss_index
    if _boss_index is None:
        data = _load_bosses()
        _boss_index = {
            boss["id"]: boss
            for boss in data.get("bosses", [])
        }
    return _boss_index


def _load_talents() -> dict:
    """加载并缓存 talents.json。"""
    global _talents_data
    if _talents_data is None:
        path = _DATA_DIR / "talents.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _talents_data = json.load(f)
        else:
            _talents_data = {"meta": {}, "talents": {}}
    return _talents_data


def _build_talent_index() -> dict[str, dict]:
    """构建 talent_id(str) → talent_info 快速查找索引。"""
    global _talent_index
    if _talent_index is None:
        data = _load_talents()
        _talent_index = data.get("talents", {})
    return _talent_index


# ============================================================
# 公开接口
# ============================================================
def get_all_classes() -> list[dict]:
    """获取所有职业列表。"""
    return _load_specs().get("classes", [])


def get_all_specs() -> list[dict]:
    """获取所有专精列表。"""
    return _load_specs().get("specs", [])


def get_spec(slug: str) -> Optional[dict]:
    """按 slug 查找单个专精，未找到返回 None。"""
    return _build_spec_index().get(slug)


def get_spell_name(spell_id: int) -> Optional[str]:
    """按 spell_id 反查技能名称，未找到返回 None。"""
    return _build_spell_index().get(spell_id)


def get_spec_spells(slug: str) -> list[dict]:
    """获取指定专精的所有技能列表，未找到返回空列表。"""
    spec = get_spec(slug)
    if spec is None:
        return []
    return spec.get("spells", [])


def get_boss(boss_id: int) -> Optional[dict]:
    """按 ID 查找 Boss，未找到返回 None。"""
    return _build_boss_index().get(boss_id)


def get_talent_name(talent_id: int, lang: str = "zh") -> Optional[str]:
    """
    按天赋节点/条目 ID 查找天赋名称。

    Args:
        talent_id: 天赋节点 ID 或条目 ID（WCL talentID）
        lang: "zh" 返回中文名，"en" 返回英文名

    Returns:
        天赋名称字符串，未找到返回 None
    """
    entry = _build_talent_index().get(str(talent_id))
    if entry is None:
        return None
    key = "name_zh" if lang == "zh" else "name_en"
    name = entry.get(key, "")
    return name if name else None


def get_talent_tree(talent_id: int) -> Optional[str]:
    """
    按天赋节点/条目 ID 查找所属子树类型。

    Args:
        talent_id: 天赋节点 ID 或条目 ID

    Returns:
        "class" / "spec" / "hero"，未找到返回 None
    """
    entry = _build_talent_index().get(str(talent_id))
    if entry is None:
        return None
    tree = entry.get("tree", "")
    return tree if tree else None


def get_spec_apl(slug: str) -> Optional[dict]:
    """
    加载指定专精的 APL 数据。

    Args:
        slug: 专精 slug，如 "balance-druid"

    Returns:
        APL 数据字典，文件不存在返回 None
    """
    apl_path = _DATA_DIR / "apl" / f"{slug}.json"
    if not apl_path.exists():
        return None
    with open(apl_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_talent_spell_id(talent_id: int) -> Optional[int]:
    """
    按天赋节点/条目 ID 查找对应的 spell_id。

    Args:
        talent_id: 天赋节点 ID 或条目 ID（WCL talentID）

    Returns:
        spell_id 整数，未找到返回 None
    """
    entry = _build_talent_index().get(str(talent_id))
    if entry is None:
        return None
    spell_id = entry.get("spell_id")
    return spell_id if spell_id else None


def get_talent_spec(talent_id: int) -> Optional[str]:
    """
    按天赋节点/条目 ID 查找所属专精名称（中文）。

    Args:
        talent_id: 天赋节点 ID 或条目 ID（WCL talentID）

    Returns:
        中文专精名称字符串（如 "平衡"），未找到返回 None
    """
    entry = _build_talent_index().get(str(talent_id))
    if entry is None:
        return None
    spec = entry.get("spec", "")
    return spec if spec else None


# 职业 → 该职业所有专精的中文名称（静态游戏数据映射）
_CLASS_SPEC_NAMES_ZH: dict[str, set[str]] = {
    "Warrior": {"武器", "狂怒", "防护"},
    "Paladin": {"神圣", "防护", "惩戒"},
    "Hunter": {"野兽控制", "射击", "生存"},
    "Rogue": {"奇袭", "狂徒", "敏锐"},
    "Priest": {"戒律", "神圣", "暗影"},
    "DeathKnight": {"鲜血", "冰霜", "邪恶"},
    "Shaman": {"元素", "增强", "恢复"},
    "Mage": {"奥术", "火焰", "冰霜"},
    "Warlock": {"痛苦", "恶魔学识", "毁灭"},
    "Monk": {"酒仙", "织雾", "踏风"},
    "Druid": {"平衡", "野性", "守护", "恢复"},
    "DemonHunter": {"浩劫", "复仇"},
    "Evoker": {"湮灭", "恩护", "增辉"},
}


def get_class_spec_names(slug: str) -> set[str]:
    """
    根据专精 slug 获取同职业所有专精的中文名称集合。

    通过 specs.json 查找 class_name，然后返回该职业的中文专精名集合。

    Args:
        slug: 专精 slug，如 "balance-druid"

    Returns:
        中文专精名称集合，如 {"平衡", "野性", "守护", "恢复"}
    """
    spec_info = get_spec(slug)
    if spec_info is None:
        return set()
    class_name = spec_info.get("class_name", "")
    return _CLASS_SPEC_NAMES_ZH.get(class_name, set())


# spell_id → talent entry_id 反查索引
_spell_to_talent: dict[int, str] | None = None


def get_talent_id_by_spell(spell_id: int) -> Optional[int]:
    """
    按 spell_id 反查对应的天赋条目 ID。

    用于判断某个技能是否是天赋授予的。

    Args:
        spell_id: 技能 ID

    Returns:
        天赋条目 ID（整数），未找到返回 None
    """
    global _spell_to_talent
    if _spell_to_talent is None:
        _spell_to_talent = {}
        for tid_str, entry in _build_talent_index().items():
            sid = entry.get("spell_id")
            if sid and sid not in _spell_to_talent:
                _spell_to_talent[sid] = tid_str
    tid_str = _spell_to_talent.get(spell_id)
    return int(tid_str) if tid_str else None
