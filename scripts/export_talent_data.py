"""
Blizzard + WCL 天赋数据导出脚本。

两阶段数据获取:
  1. Blizzard Game Data API — 获取天赋树节点，建立 node_id → 中文/英文名称映射
  2. WCL CombatantInfo — 获取 WCL 内部 entry ID → Blizzard node ID 桥接映射

WCL characterRankings.talents[].talentID 使用 WCL 内部 entry ID，
而非 Blizzard node ID。CombatantInfo.talentTree 同时包含两种 ID:
  {id: WCL_ENTRY_ID, nodeID: BLIZZARD_NODE_ID, rank: N}

本脚本将两种 ID 都作为 key 写入 talents.json，实现统一解析。

用法:
  # 设置环境变量（或写入 .env 文件）
  export BNET_CLIENT_ID=your_client_id
  export BNET_CLIENT_SECRET=your_client_secret
  export WCL_CLIENT_ID=your_wcl_client_id
  export WCL_CLIENT_SECRET=your_wcl_client_secret
  python scripts/export_talent_data.py

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 第三方库
# ============================================================
import httpx

# ============================================================
# 尝试从 .env 文件加载环境变量
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# 常量
# ============================================================
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "data"
OUTPUT_FILE = OUTPUT_DIR / "talents.json"

# ---------- Blizzard API ----------
OAUTH_URL = "https://oauth.battle.net/token"
API_BASE = "https://us.api.blizzard.com"
NAMESPACE = "static-us"

# ---------- WCL API ----------
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_API_URL = "https://www.warcraftlogs.com/api/v2/client"

# 请求间隔（秒），避免触发速率限制
REQUEST_DELAY = 0.15
WCL_REQUEST_DELAY = 0.5

# ---------- WCL 专精 ID 映射 ----------
# WCL 使用 Blizzard spec ID 作为 className/specName 查询参数
# 这里提供 slug → (className, specName) 映射，用于 WCL 排行查询
SPEC_WCL_MAP: dict[str, tuple[str, str]] = {
    "blood-death-knight": ("DeathKnight", "Blood"),
    "frost-death-knight": ("DeathKnight", "Frost"),
    "unholy-death-knight": ("DeathKnight", "Unholy"),
    "havoc-demon-hunter": ("DemonHunter", "Havoc"),
    "vengeance-demon-hunter": ("DemonHunter", "Vengeance"),
    "devourer-demon-hunter": ("DemonHunter", "Devourer"),
    "balance-druid": ("Druid", "Balance"),
    "feral-druid": ("Druid", "Feral"),
    "guardian-druid": ("Druid", "Guardian"),
    "restoration-druid": ("Druid", "Restoration"),
    "augmentation-evoker": ("Evoker", "Augmentation"),
    "devastation-evoker": ("Evoker", "Devastation"),
    "preservation-evoker": ("Evoker", "Preservation"),
    "beast-mastery-hunter": ("Hunter", "BeastMastery"),
    "marksmanship-hunter": ("Hunter", "Marksmanship"),
    "survival-hunter": ("Hunter", "Survival"),
    "arcane-mage": ("Mage", "Arcane"),
    "fire-mage": ("Mage", "Fire"),
    "frost-mage": ("Mage", "Frost"),
    "brewmaster-monk": ("Monk", "Brewmaster"),
    "mistweaver-monk": ("Monk", "Mistweaver"),
    "windwalker-monk": ("Monk", "Windwalker"),
    "holy-paladin": ("Paladin", "Holy"),
    "protection-paladin": ("Paladin", "Protection"),
    "retribution-paladin": ("Paladin", "Retribution"),
    "discipline-priest": ("Priest", "Discipline"),
    "holy-priest": ("Priest", "Holy"),
    "shadow-priest": ("Priest", "Shadow"),
    "assassination-rogue": ("Rogue", "Assassination"),
    "outlaw-rogue": ("Rogue", "Outlaw"),
    "subtlety-rogue": ("Rogue", "Subtlety"),
    "elemental-shaman": ("Shaman", "Elemental"),
    "enhancement-shaman": ("Shaman", "Enhancement"),
    "restoration-shaman": ("Shaman", "Restoration"),
    "affliction-warlock": ("Warlock", "Affliction"),
    "demonology-warlock": ("Warlock", "Demonology"),
    "destruction-warlock": ("Warlock", "Destruction"),
    "arms-warrior": ("Warrior", "Arms"),
    "fury-warrior": ("Warrior", "Fury"),
    "protection-warrior": ("Warrior", "Protection"),
}

# Blizzard spec ID → slug（用于从 CombatantInfo 的 specID 匹配）
BNET_SPEC_ID_MAP: dict[int, str] = {
    250: "blood-death-knight", 251: "frost-death-knight",
    252: "unholy-death-knight",
    577: "havoc-demon-hunter", 581: "vengeance-demon-hunter",
    1473: "devourer-demon-hunter",
    102: "balance-druid", 103: "feral-druid",
    104: "guardian-druid", 105: "restoration-druid",
    1473: "devourer-demon-hunter",
    1468: "augmentation-evoker", 1467: "devastation-evoker",
    1465: "preservation-evoker",
    253: "beast-mastery-hunter", 254: "marksmanship-hunter",
    255: "survival-hunter",
    62: "arcane-mage", 63: "fire-mage", 64: "frost-mage",
    268: "brewmaster-monk", 270: "mistweaver-monk",
    269: "windwalker-monk",
    65: "holy-paladin", 66: "protection-paladin",
    70: "retribution-paladin",
    256: "discipline-priest", 257: "holy-priest",
    258: "shadow-priest",
    259: "assassination-rogue", 260: "outlaw-rogue",
    261: "subtlety-rogue",
    262: "elemental-shaman", 263: "enhancement-shaman",
    264: "restoration-shaman",
    265: "affliction-warlock", 266: "demonology-warlock",
    267: "destruction-warlock",
    71: "arms-warrior", 72: "fury-warrior",
    73: "protection-warrior",
}


# ============================================================
# 日志辅助
# ============================================================
def _log(msg: str) -> None:
    """输出进度信息到 stderr。"""
    print(msg, file=sys.stderr)


# ============================================================
# Blizzard OAuth 认证
# ============================================================
async def _get_bnet_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
) -> str:
    """通过 client_credentials 获取暴雪 OAuth 访问令牌。"""
    resp = await client.post(
        OAUTH_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _log(f"  Blizzard OAuth 令牌获取成功 (前8位: {token[:8]}...)")
    return token


# ============================================================
# WCL OAuth 认证
# ============================================================
async def _get_wcl_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
) -> str:
    """通过 client_credentials 获取 WCL OAuth 访问令牌。"""
    resp = await client.post(
        WCL_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _log(f"  WCL OAuth 令牌获取成功 (前8位: {token[:8]}...)")
    return token


# ============================================================
# Blizzard API 请求封装
# ============================================================
async def _api_get(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    locale: str = "zh_CN",
) -> dict[str, Any]:
    """
    向暴雪 API 发起 GET 请求。

    自动处理速率限制（429）重试。
    """
    url = f"{API_BASE}{path}"
    params = {"namespace": NAMESPACE, "locale": locale}
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(3):
        await asyncio.sleep(REQUEST_DELAY)
        resp = await client.get(url, params=params, headers=headers)

        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            _log(f"  速率限制，等待 {wait}s 后重试...")
            await asyncio.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"请求失败（3 次重试后）: {path}")


# ============================================================
# WCL GraphQL 请求封装
# ============================================================
async def _wcl_query(
    client: httpx.AsyncClient,
    token: str,
    graphql: str,
) -> dict[str, Any]:
    """
    向 WCL GraphQL API 发起查询。

    自动处理速率限制重试。
    """
    full_query = f"query {{ {graphql} }}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(3):
        await asyncio.sleep(WCL_REQUEST_DELAY)
        resp = await client.post(
            WCL_API_URL,
            json={"query": full_query},
            headers=headers,
        )

        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            _log(f"  WCL 速率限制，等待 {wait}s 后重试...")
            await asyncio.sleep(wait)
            continue

        resp.raise_for_status()
        result = resp.json()

        # 检查 GraphQL 错误
        errors = result.get("errors")
        if errors:
            msgs = [e.get("message", str(e)) for e in errors]
            raise RuntimeError(f"WCL GraphQL 错误: {'; '.join(msgs)}")

        return result.get("data", {})

    raise RuntimeError("WCL 请求失败（3 次重试后）")


# ============================================================
# 天赋节点解析 — 从单个 node 提取所有 ID → 名称映射
# ============================================================
def _extract_from_node(
    node: dict[str, Any],
    spec_slug: str,
    locale: str,
    tree_type: str = "",
) -> list[dict[str, Any]]:
    """
    从天赋节点提取 ID → 名称映射。

    处理多种响应格式:
    - ranks[].tooltip.spell_tooltip.spell
    - ranks[].choice_of_tooltips[].spell_tooltip.spell
    - 直接 tooltip.spell_tooltip.spell
    """
    results: list[dict[str, Any]] = []
    node_id = node.get("id")

    # 收集所有需要处理的 entry
    entries = node.get("entries", [])
    ranks = node.get("ranks", [])

    # ---------- 从 entries 提取 ----------
    for entry in entries:
        entry_id = entry.get("id")
        definition_id = entry.get("definitionId")
        spell_info = _dig_spell(entry)
        if spell_info:
            record = {
                "node_id": node_id,
                "entry_id": entry_id,
                "definition_id": definition_id,
                "spell_id": spell_info["id"],
                "name": spell_info["name"],
                "spec": spec_slug,
                "locale": locale,
                "tree": tree_type,
            }
            results.append(record)

    # ---------- 从 ranks 提取 ----------
    for rank in ranks:
        # 标准格式: tooltip.spell_tooltip.spell
        spell_info = _dig_spell(rank)
        if spell_info:
            results.append({
                "node_id": node_id,
                "entry_id": None,
                "definition_id": None,
                "spell_id": spell_info["id"],
                "name": spell_info["name"],
                "spec": spec_slug,
                "locale": locale,
                "tree": tree_type,
            })

        # 选择节点: choice_of_tooltips
        choices = rank.get("choice_of_tooltips", [])
        for choice in choices:
            cs = _dig_spell_from_tooltip(choice)
            if cs:
                results.append({
                    "node_id": node_id,
                    "entry_id": None,
                    "definition_id": None,
                    "spell_id": cs["id"],
                    "name": cs["name"],
                    "spec": spec_slug,
                    "locale": locale,
                    "tree": tree_type,
                })

    return results


def _dig_spell(obj: dict[str, Any]) -> dict[str, Any] | None:
    """从任意层级对象中挖掘 spell 信息（id + name）。"""
    # 路径1: tooltip.spell_tooltip.spell
    result = _dig_spell_from_tooltip(obj.get("tooltip", {}))
    if result:
        return result

    # 路径2: spell_tooltip.spell（直接在对象上）
    result = _dig_spell_from_tooltip(obj)
    if result:
        return result

    # 路径3: spell 直接在对象上
    spell = obj.get("spell")
    if isinstance(spell, dict) and spell.get("id") and spell.get("name"):
        return {"id": spell["id"], "name": spell["name"]}

    return None


def _dig_spell_from_tooltip(tooltip: dict[str, Any]) -> dict[str, Any] | None:
    """从 tooltip 结构中提取 spell_tooltip.spell。"""
    spell_tooltip = tooltip.get("spell_tooltip", {})
    spell = spell_tooltip.get("spell", {})
    if spell.get("id") and spell.get("name"):
        return {"id": spell["id"], "name": spell["name"]}
    return None


# ============================================================
# Blizzard 天赋树数据获取
# ============================================================
async def _fetch_blizzard_talents(
    client: httpx.AsyncClient,
    token: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    从 Blizzard API 获取天赋树数据。

    返回 (zh_records, en_records) 原始记录列表。
    """
    # 获取天赋树索引
    _log("\n[2/6] 获取天赋树索引...")
    index = await _api_get(client, "/data/wow/talent-tree/index", token)

    # 提取所有 (tree_id, spec_id) 对
    tree_specs: list[tuple[int, int, str]] = []
    spec_trees = index.get("spec_talent_trees", [])
    for entry in spec_trees:
        href = entry.get("key", {}).get("href", "")
        parts = href.split("/")
        try:
            tt_idx = parts.index("talent-tree")
            ps_idx = parts.index("playable-specialization")
            tree_id = int(parts[tt_idx + 1])
            spec_id_str = parts[ps_idx + 1].split("?")[0]
            spec_id = int(spec_id_str)
            spec_name = entry.get("name", f"spec-{spec_id}")
            tree_specs.append((tree_id, spec_id, spec_name))
        except (ValueError, IndexError):
            _log(f"  [警告] 无法解析 href: {href}")

    _log(f"  发现 {len(tree_specs)} 个专精天赋树")

    zh_records: list[dict[str, Any]] = []
    en_records: list[dict[str, Any]] = []

    # 逐个获取天赋树详情（中文 + 英文）
    _log("\n[3/6] 获取天赋树详情（中文 + 英文）...")
    for i, (tree_id, spec_id, spec_name) in enumerate(tree_specs, 1):
        _log(f"  [{i}/{len(tree_specs)}] {spec_name} "
             f"(tree={tree_id}, spec={spec_id})")

        path = (
            f"/data/wow/talent-tree/{tree_id}"
            f"/playable-specialization/{spec_id}"
        )
        slug = _name_to_slug(spec_name)

        # 中文版本
        try:
            zh_data = await _api_get(client, path, token, locale="zh_CN")
            zh_records.extend(
                _parse_talent_tree(zh_data, slug, "zh_CN")
            )
        except Exception as exc:
            _log(f"    [警告] zh_CN 获取失败: {exc}")

        # 英文版本
        try:
            en_data = await _api_get(client, path, token, locale="en_US")
            en_records.extend(
                _parse_talent_tree(en_data, slug, "en_US")
            )
        except Exception as exc:
            _log(f"    [警告] en_US 获取失败: {exc}")

    # 尝试获取英雄天赋树
    _log("\n  尝试获取英雄天赋树...")
    try:
        hero_index = await _api_get(
            client, "/data/wow/tech-talent-tree/index", token,
        )
        hero_trees = hero_index.get("talent_trees", [])
        for ht in hero_trees:
            ht_id = ht.get("id")
            if not ht_id:
                continue
            try:
                zh_hero = await _api_get(
                    client,
                    f"/data/wow/tech-talent-tree/{ht_id}",
                    token, locale="zh_CN",
                )
                zh_records.extend(_parse_hero_tree(zh_hero, "zh_CN"))
                en_hero = await _api_get(
                    client,
                    f"/data/wow/tech-talent-tree/{ht_id}",
                    token, locale="en_US",
                )
                en_records.extend(_parse_hero_tree(en_hero, "en_US"))
            except Exception:
                pass  # 英雄天赋端点可能不存在
    except Exception as exc:
        _log(f"  [提示] 英雄天赋树索引不可用: {exc}")

    return zh_records, en_records


def _parse_talent_tree(
    data: dict[str, Any],
    spec_slug: str,
    locale: str,
) -> list[dict[str, Any]]:
    """解析天赋树 API 响应，提取所有节点记录。"""
    records: list[dict[str, Any]] = []

    # 类天赋节点
    for node in data.get("class_talent_nodes", []):
        records.extend(_extract_from_node(node, spec_slug, locale, "class"))

    # 专精天赋节点
    for node in data.get("spec_talent_nodes", []):
        records.extend(_extract_from_node(node, spec_slug, locale, "spec"))

    # 英雄天赋节点（直接在响应中，旧格式）
    for node in data.get("hero_talent_nodes", []):
        records.extend(_extract_from_node(node, spec_slug, locale, "hero"))

    # 英雄天赋树（嵌套在 hero_talent_trees[].hero_talent_nodes 中）
    for hero_tree in data.get("hero_talent_trees", []):
        for node in hero_tree.get("hero_talent_nodes", []):
            records.extend(_extract_from_node(node, spec_slug, locale, "hero"))

    return records


def _parse_hero_tree(
    data: dict[str, Any],
    locale: str,
) -> list[dict[str, Any]]:
    """解析英雄天赋树（tech-talent-tree）响应。"""
    records: list[dict[str, Any]] = []
    talents = data.get("talent_nodes", data.get("talents", []))
    for node in talents:
        records.extend(_extract_from_node(node, "hero", locale, "hero"))
    return records


def _name_to_slug(name: str) -> str:
    """将专精名称转换为 slug。"""
    return name.lower().replace(" ", "-").replace("'", "")


# ============================================================
# WCL CombatantInfo 获取 — 建立 WCL entry ID → Blizzard node ID 桥接
# ============================================================
# 每个专精采样的最大报告数，覆盖更多天赋变体
SAMPLES_PER_SPEC = 8


async def _fetch_wcl_talent_bridge(
    client: httpx.AsyncClient,
    wcl_token: str,
) -> dict[int, int]:
    """
    从 WCL CombatantInfo 获取 WCL entry ID → Blizzard node ID 映射。

    流程:
    1. 对每个专精，查询多个 top ranking 获取不同的 reportCode + fightID
    2. 获取每个 fight 的 CombatantInfo 事件
    3. 从 talentTree 字段提取 {id, nodeID} 映射

    采样多个玩家以覆盖弹性天赋节点。

    返回: {wcl_entry_id: blizzard_node_id}
    """
    _log("\n[4/6] 从 WCL CombatantInfo 获取 ID 桥接映射...")
    _log(f"  每个专精采样 {SAMPLES_PER_SPEC} 个报告")

    bridge: dict[int, int] = {}
    specs = list(SPEC_WCL_MAP.items())
    total = len(specs)

    for i, (slug, (class_name, spec_name)) in enumerate(specs, 1):
        _log(f"  [{i}/{total}] {slug}...")
        try:
            rankings = await _fetch_multiple_rankings(
                client, wcl_token, class_name, spec_name,
                max_reports=SAMPLES_PER_SPEC,
            )
            if not rankings:
                _log(f"    [跳过] 无排名数据")
                continue

            spec_new = 0
            for rank_info in rankings:
                report_code = rank_info["report_code"]
                fight_id = rank_info["fight_id"]

                talent_trees = await _fetch_all_combatant_talent_trees(
                    client, wcl_token, report_code, fight_id, slug,
                )
                for talent_tree in talent_trees:
                    for entry in talent_tree:
                        wcl_id = entry.get("id")
                        node_id = entry.get("nodeID")
                        if wcl_id and node_id and wcl_id not in bridge:
                            bridge[wcl_id] = node_id
                            spec_new += 1

            _log(f"    {len(rankings)} 个报告，新增 {spec_new} 条桥接")

        except Exception as exc:
            _log(f"    [警告] 获取失败: {exc}")

    _log(f"  WCL 桥接映射总计: {len(bridge)} 条")
    return bridge


# 桥接采样使用多个 encounter，覆盖不同天赋选择
# 混合不同 raid/dungeon boss 以最大化天赋变体覆盖
BRIDGE_ENCOUNTER_IDS = [3178, 3009, 3181]


async def _fetch_multiple_rankings(
    client: httpx.AsyncClient,
    token: str,
    class_name: str,
    spec_name: str,
    *,
    max_reports: int = SAMPLES_PER_SPEC,
) -> list[dict[str, Any]]:
    """
    获取指定专精的多个不同报告的 top ranking。

    跨多个 encounter 查询，去重 report_code，覆盖不同玩家的天赋选择。

    返回: [{report_code, fight_id}, ...]
    """
    seen_codes: set[str] = set()
    results: list[dict[str, Any]] = []

    # 查询多种来源: 不同 encounter × 不同难度/页码
    # 覆盖 meta 构建（heroic p1）和实验构建（heroic p2, normal p1）
    query_variants = [
        (4, 1),  # heroic page 1 — meta builds
        (4, 2),  # heroic page 2 — slightly off-meta
        (3, 1),  # normal page 1 — experimental builds
    ]

    for enc_id in BRIDGE_ENCOUNTER_IDS:
        if len(results) >= max_reports:
            break

        for difficulty, page in query_variants:
            if len(results) >= max_reports:
                break

            graphql = f"""
            worldData {{
                encounter(id: {enc_id}) {{
                    characterRankings(
                        className: "{class_name}"
                        specName: "{spec_name}"
                        metric: dps
                        difficulty: {difficulty}
                        page: {page}
                    )
                }}
            }}
            """
            try:
                data = await _wcl_query(client, token, graphql)
            except Exception:
                continue

            rankings_data = (
                data.get("worldData", {})
                .get("encounter", {})
                .get("characterRankings", {})
            )
            rankings = rankings_data.get("rankings", [])

            for r in rankings:
                report = r.get("report", {})
                code = report.get("code")
                fight_id = report.get("fightID")
                if code and fight_id and code not in seen_codes:
                    seen_codes.add(code)
                    results.append({"report_code": code, "fight_id": fight_id})
                    if len(results) >= max_reports:
                        break

    return results


async def _fetch_all_combatant_talent_trees(
    client: httpx.AsyncClient,
    token: str,
    report_code: str,
    fight_id: int,
    spec_slug: str,
) -> list[list[dict[str, Any]]]:
    """
    获取指定 report + fight 中所有匹配专精玩家的 talentTree。

    返回多个天赋树列表，每个玩家一个: [[{id, nodeID, rank}, ...], ...]
    不同玩家可能选择了不同的弹性天赋，全部收集以最大化桥接覆盖。
    """
    target_spec_ids: list[int] = [
        sid for sid, s in BNET_SPEC_ID_MAP.items()
        if s == spec_slug
    ]

    graphql = f"""
    reportData {{
        report(code: "{report_code}") {{
            events(
                startTime: 0
                endTime: 999999999
                fightIDs: [{fight_id}]
                dataType: CombatantInfo
                limit: 50
            ) {{
                data
            }}
        }}
    }}
    """
    data = await _wcl_query(client, token, graphql)

    events = (
        data.get("reportData", {})
        .get("report", {})
        .get("events", {})
        .get("data", [])
    )

    # 收集所有匹配专精的天赋树
    trees: list[list[dict[str, Any]]] = []
    for event in events:
        spec_id = event.get("specID")
        if spec_id in target_spec_ids:
            tree = event.get("talentTree", [])
            if tree:
                trees.append(tree)

    # 如果没找到精确匹配，返回所有有 talentTree 的事件
    if not trees:
        for event in events:
            tree = event.get("talentTree", [])
            if tree:
                trees.append(tree)

    return trees

    return []


# ============================================================
# 合并中英文记录 + WCL 桥接
# ============================================================
def _merge_records(
    zh_records: list[dict[str, Any]],
    en_records: list[dict[str, Any]],
    wcl_bridge: dict[int, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    合并中英文记录，构建最终映射。

    每个 ID（node_id, entry_id, definition_id）各作为一个 key。
    如果提供了 wcl_bridge，还会将 WCL entry ID 作为额外的 key。
    """
    # 先建英文索引: spell_id → en_name
    en_by_spell: dict[int, str] = {}
    for rec in en_records:
        spell_id = rec.get("spell_id")
        if spell_id:
            en_by_spell[spell_id] = rec["name"]

    # 构建最终映射
    talents: dict[str, dict[str, Any]] = {}

    # 建英文 tree 索引: spell_id → tree
    en_tree_by_spell: dict[int, str] = {}
    for rec in en_records:
        spell_id = rec.get("spell_id")
        tree = rec.get("tree", "")
        if spell_id and tree:
            en_tree_by_spell[spell_id] = tree

    for rec in zh_records:
        spell_id = rec.get("spell_id")
        zh_name = rec["name"]
        en_name = en_by_spell.get(spell_id, "")
        spec_slug = rec.get("spec", "")
        tree = rec.get("tree", "") or en_tree_by_spell.get(spell_id, "")

        entry = {
            "name_zh": zh_name,
            "name_en": en_name,
            "spell_id": spell_id,
            "spec": spec_slug,
            "tree": tree,
        }

        # 将所有可能的 ID 作为 key 写入
        for id_key in ("node_id", "entry_id", "definition_id"):
            raw_id = rec.get(id_key)
            if raw_id is not None:
                str_id = str(raw_id)
                # 如果已存在，保留已有记录（避免覆盖更精确的数据）
                if str_id not in talents:
                    talents[str_id] = entry

    # 补充英文记录中有但中文没有的
    for rec in en_records:
        en_name = rec["name"]
        spell_id = rec.get("spell_id")
        spec_slug = rec.get("spec", "")
        tree = rec.get("tree", "")

        entry = {
            "name_zh": "",
            "name_en": en_name,
            "spell_id": spell_id,
            "spec": spec_slug,
            "tree": tree,
        }

        for id_key in ("node_id", "entry_id", "definition_id"):
            raw_id = rec.get(id_key)
            if raw_id is not None:
                str_id = str(raw_id)
                if str_id not in talents:
                    talents[str_id] = entry

    # ---------- 通过 WCL 桥接添加额外的 key ----------
    if wcl_bridge:
        _add_wcl_bridge_keys(talents, wcl_bridge)

    return talents


def _add_wcl_bridge_keys(
    talents: dict[str, dict[str, Any]],
    wcl_bridge: dict[int, int],
) -> None:
    """
    将 WCL entry ID 作为额外的 key 添加到天赋映射中。

    通过桥接: wcl_entry_id → blizzard_node_id → 已有天赋记录
    """
    added = 0
    for wcl_id, node_id in wcl_bridge.items():
        str_wcl_id = str(wcl_id)
        str_node_id = str(node_id)

        # 如果 WCL ID 已经存在，跳过（可能与 Blizzard ID 重叠）
        if str_wcl_id in talents:
            continue

        # 查找对应的 Blizzard node 记录
        node_entry = talents.get(str_node_id)
        if node_entry:
            talents[str_wcl_id] = node_entry
            added += 1

    _log(f"  WCL 桥接新增 {added} 个 key")


# ============================================================
# 导出 JSON
# ============================================================
def _write_output(talents: dict[str, dict[str, Any]]) -> Path:
    """将天赋映射写入 JSON 文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "source": "Blizzard Game Data API + WCL CombatantInfo",
            "locale": "zh_CN",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(talents),
        },
        "talents": talents,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return OUTPUT_FILE


# ============================================================
# 主入口
# ============================================================
async def main() -> None:
    """执行完整的天赋数据导出流程。"""
    _log("=" * 60)
    _log("Blizzard + WCL 天赋数据导出脚本")
    _log("=" * 60)

    # ---------- 读取凭证 ----------
    bnet_id = os.environ.get("BNET_CLIENT_ID", "")
    bnet_secret = os.environ.get("BNET_CLIENT_SECRET", "")
    if not bnet_id or not bnet_secret:
        _log("\n[错误] 请设置 BNET_CLIENT_ID 和 BNET_CLIENT_SECRET")
        sys.exit(1)

    wcl_id = os.environ.get("WCL_CLIENT_ID", "")
    wcl_secret = os.environ.get("WCL_CLIENT_SECRET", "")
    has_wcl = bool(wcl_id and wcl_secret)
    if not has_wcl:
        _log("\n[警告] 未设置 WCL_CLIENT_ID/WCL_CLIENT_SECRET，"
             "跳过 WCL 桥接映射")

    start = time.monotonic()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 获取 Blizzard 令牌
        _log("\n[1/6] 获取 Blizzard OAuth 令牌...")
        bnet_token = await _get_bnet_token(client, bnet_id, bnet_secret)

        # 获取 Blizzard 天赋数据
        zh_records, en_records = await _fetch_blizzard_talents(
            client, bnet_token,
        )

        # 获取 WCL 桥接映射（如果有凭证）
        wcl_bridge: dict[int, int] | None = None
        if has_wcl:
            _log("\n  获取 WCL OAuth 令牌...")
            wcl_token = await _get_wcl_token(client, wcl_id, wcl_secret)
            wcl_bridge = await _fetch_wcl_talent_bridge(
                client, wcl_token,
            )

    # 合并数据
    _log("\n[5/6] 合并数据...")
    talents = _merge_records(zh_records, en_records, wcl_bridge)

    # 写入文件
    _log("\n[6/6] 写入 JSON...")
    path = _write_output(talents)
    elapsed = time.monotonic() - start

    _log(f"\n{'=' * 60}")
    _log("导出完成:")
    _log(f"  天赋条目: {len(talents)}")
    _log(f"  输出文件: {path}")
    _log(f"  耗时: {elapsed:.1f}s")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
