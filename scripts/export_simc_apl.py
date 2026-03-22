"""
SimulationCraft APL 导出脚本 — 解析 SimC APL 文件并输出 JSON。

从 SimC 代码库的 class_modules 或 profiles 目录中解析 APL 定义，
提取每条 action 的技能名称和条件，输出为结构化 JSON。

v1 范围: 仅支持 Balance Druid。

用法:
  uv run python scripts/export_simc_apl.py --simc-path /path/to/simc

输出:
  src/data/apl/{spec_slug}.json

SimC APL 格式示例:
  actions+=/spell_name,if=buff.X.up&cooldown.Y.remains<5

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import argparse
import json
import re
import sys
from pathlib import Path

# ============================================================
# 常量
# ============================================================

# v1: 支持的专精和对应的 SimC APL 文件路径模式
SPEC_APL_FILES: dict[str, list[str]] = {
    "balance-druid": [
        "ActionPriorityLists/default/druid_balance.simc",
        "ActionPriorityLists/assisted_combat/druid_balance.simc",
        "engine/class_modules/apl/druid/balance.simc",
        "profiles/Tier31/T31_Druid_Balance.simc",
        "engine/class_modules/apl/apl_druid.cpp",
    ],
}

# 输出目录
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "data" / "apl"

# APL action 行正则: actions[.list]+=/spell,conditions
_ACTION_PATTERN = re.compile(
    r"^\s*actions"           # 起始
    r"(?:\.(\w+))?"          # 可选的 action_list 名称
    r"\+?=/?"                # 赋值操作符
    r"(\w+)"                 # 技能名称
    r"(.*?)$"                # 剩余部分（条件等）
)

# 条件解析: if=... 部分
_IF_PATTERN = re.compile(r",if=(.*?)(?:,|$)")

# C++ APL 行正则: default_->add_action("spell","if=...")
_CPP_ACTION_PATTERN = re.compile(
    r'(?:default_|apl_\w+)\s*->\s*add_action\(\s*'
    r'"([^"]+)"'             # 技能名称
    r'(?:\s*,\s*"([^"]*)")?'  # 可选的条件字符串
)

# C++ APL list 正则: auto apl_xxx = ...
_CPP_LIST_PATTERN = re.compile(
    r'auto\s+(apl_\w+)\s*='
)

# C++ 常见 action_list 创建
_CPP_GET_APL_PATTERN = re.compile(
    r'get_action_priority_list\(\s*"(\w+)"'
)


# ============================================================
# 条件解析
# ============================================================


def _parse_conditions(cond_str: str) -> list[str]:
    """
    将 SimC 条件字符串解析为条件列表。

    例: "buff.X.up&cooldown.Y.remains<5" -> ["buff.X.up", "cooldown.Y.remains<5"]
    """
    if not cond_str:
        return []

    # 按 & 和 | 分割（保留操作符信息在各条件中）
    conditions: list[str] = []
    # 简单分割: 按 & 分割顶层条件
    parts = re.split(r"[&|]", cond_str)
    for p in parts:
        p = p.strip().strip("()")
        if p:
            conditions.append(p)

    return conditions


def _parse_action_params(remainder: str) -> dict:
    """解析 action 行中的逗号分隔参数。"""
    params: dict[str, str] = {}
    if not remainder:
        return params

    # 去掉开头的逗号
    remainder = remainder.lstrip(",")
    for part in remainder.split(","):
        part = part.strip()
        if "=" in part:
            key, _, val = part.partition("=")
            params[key.strip()] = val.strip()

    return params


# ============================================================
# .simc 文件解析
# ============================================================


def _parse_simc_file(filepath: Path) -> list[dict]:
    """
    解析 .simc APL 文件，提取 action 行。

    返回 [{spell, conditions, action_list, raw_line, priority}]
    """
    rules: list[dict] = []
    priority = 0

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            match = _ACTION_PATTERN.match(line)
            if not match:
                continue

            action_list = match.group(1) or "default"
            spell = match.group(2)
            remainder = match.group(3)

            # 跳过非技能 action（call_action_list, variable, etc）
            if spell in ("call_action_list", "run_action_list",
                         "variable", "snapshot_stats", "potion",
                         "use_item", "use_items", "flask", "food",
                         "augmentation"):
                continue

            params = _parse_action_params(remainder)
            conditions = _parse_conditions(params.get("if", ""))

            priority += 1
            rules.append({
                "spell": spell,
                "conditions": conditions,
                "action_list": action_list,
                "raw_line": line,
                "priority": priority,
            })

    return rules


# ============================================================
# C++ APL 文件解析
# ============================================================


def _parse_cpp_apl(filepath: Path, spec_keyword: str) -> list[dict]:
    """
    从 SimC C++ 文件中解析 APL 定义。

    C++ 格式: default_->add_action("spell","if=conditions")
    """
    rules: list[dict] = []
    priority = 0
    current_list = "default"
    in_spec_section = False

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()

            # 检测专精区块
            if spec_keyword in stripped.lower():
                in_spec_section = True
            elif in_spec_section and stripped.startswith("void") and spec_keyword not in stripped.lower():
                in_spec_section = False

            if not in_spec_section:
                continue

            # 检测 action list 名称
            list_match = _CPP_GET_APL_PATTERN.search(stripped)
            if list_match:
                current_list = list_match.group(1)
                continue

            # 检测 action
            action_match = _CPP_ACTION_PATTERN.search(stripped)
            if not action_match:
                continue

            spell = action_match.group(1)
            cond_str = action_match.group(2) or ""

            # 跳过非技能 action
            if spell in ("call_action_list", "run_action_list",
                         "variable", "snapshot_stats", "potion",
                         "use_item", "use_items"):
                continue

            # 解析 if= 条件
            conditions: list[str] = []
            if cond_str.startswith("if="):
                conditions = _parse_conditions(cond_str[3:])
            elif "if=" in cond_str:
                if_match = _IF_PATTERN.search("," + cond_str)
                if if_match:
                    conditions = _parse_conditions(if_match.group(1))

            priority += 1
            rules.append({
                "spell": spell,
                "conditions": conditions,
                "action_list": current_list,
                "raw_line": stripped,
                "priority": priority,
            })

    return rules


# ============================================================
# 主流程
# ============================================================


def export_spec_apl(simc_path: Path, spec_slug: str) -> Path:
    """
    导出指定专精的 APL 为 JSON。

    按优先级搜索 APL 文件（.simc 优先于 .cpp）。
    """
    apl_files = SPEC_APL_FILES.get(spec_slug, [])
    rules: list[dict] = []

    for rel_path in apl_files:
        filepath = simc_path / rel_path
        if not filepath.exists():
            continue

        if filepath.suffix == ".simc":
            rules = _parse_simc_file(filepath)
        elif filepath.suffix == ".cpp":
            # 从 spec_slug 提取关键词用于定位 C++ 中的区块
            spec_keyword = spec_slug.split("-")[0]  # e.g. "balance"
            rules = _parse_cpp_apl(filepath, spec_keyword)

        if rules:
            break

    if not rules:
        print(f"警告: 未找到 {spec_slug} 的 APL 文件")
        return Path()

    # 输出 JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{spec_slug}.json"
    output = {
        "spec": spec_slug,
        "version": "v1",
        "source": str(apl_files),
        "action_lists": _group_by_list(rules),
        "rules": rules,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"已导出 {spec_slug}: {len(rules)} 条规则 → {output_path}")
    return output_path


def _group_by_list(rules: list[dict]) -> dict[str, list[str]]:
    """按 action_list 分组，返回每个列表中的技能顺序。"""
    groups: dict[str, list[str]] = {}
    for r in rules:
        al = r["action_list"]
        if al not in groups:
            groups[al] = []
        groups[al].append(r["spell"])
    return groups


def main() -> None:
    parser = argparse.ArgumentParser(
        description="解析 SimC APL 文件，导出 JSON"
    )
    parser.add_argument(
        "--simc-path",
        type=Path,
        required=True,
        help="SimulationCraft 代码库根目录路径",
    )
    parser.add_argument(
        "--spec",
        type=str,
        default="balance-druid",
        help="专精 slug（默认: balance-druid）",
    )
    args = parser.parse_args()

    if not args.simc_path.exists():
        print(f"错误: SimC 路径不存在: {args.simc_path}", file=sys.stderr)
        sys.exit(1)

    export_spec_apl(args.simc_path, args.spec)


if __name__ == "__main__":
    main()
