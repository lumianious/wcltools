"""
get_encounters 工具 — 发现当前版本的副本和 Boss。

查询 WCL worldData.expansion 获取区域和遭遇列表。
缓存 24 小时（遭遇数据几乎不变）。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import json
import logging
from typing import Literal, Optional

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.models import Encounter, EncountersResponse, Zone
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

# Midnight 资料片 ID — WCL expansion ID
# WCL 复用了 expansion ID: Midnight = 7（与 Legion 相同 slot）
# fallback 机制会动态发现正确 ID
CURRENT_EXPANSION_ID = 7

CACHE_TTL_SECONDS = 24 * 3600  # 24 小时


# ============================================================
# 核心查询
# ============================================================

async def _query_expansion_zones(
    client: WCLClient, expansion_id: int
) -> list[dict]:
    """
    查询指定资料片的全部区域和遭遇。

    Returns:
        zones 原始列表
    """
    gql = f"""
        worldData {{
            expansion(id: {expansion_id}) {{
                name
                zones {{
                    id
                    name
                    encounters {{
                        id
                        name
                    }}
                }}
            }}
        }}
    """
    data = await client.query(gql)
    expansion_data = data.get("worldData", {}).get("expansion")
    if not expansion_data:
        return []
    return expansion_data.get("zones") or []


async def _discover_current_expansion_id(
    client: WCLClient,
) -> int:
    """
    动态发现最新资料片 ID。

    尝试 CURRENT_EXPANSION_ID，失败则向下搜索。
    """
    # 先尝试已知 ID
    zones = await _query_expansion_zones(client, CURRENT_EXPANSION_ID)
    if zones:
        return CURRENT_EXPANSION_ID

    # fallback: 从高到低搜索
    for eid in range(15, 0, -1):
        if eid == CURRENT_EXPANSION_ID:
            continue
        zones = await _query_expansion_zones(client, eid)
        if zones:
            logger.info("发现资料片 ID: %d", eid)
            return eid

    raise RuntimeError("无法发现任何有效的资料片 ID")


# ============================================================
# 公开接口
# ============================================================


async def get_encounters(
    client: WCLClient,
    content_type: Optional[Literal["raid", "mythic_plus", "all"]] = "all",
) -> EncountersResponse:
    """
    获取当前版本的副本区域和 Boss 列表。

    Args:
        client: WCL API 客户端
        content_type: 过滤类型 — "raid" / "mythic_plus" / "all"

    Returns:
        EncountersResponse 包含区域和遭遇列表
    """
    cache_key = f"encounters:{content_type}"

    # 尝试缓存
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_encounters 缓存命中")
        return EncountersResponse(**cached)

    # 发现资料片
    exp_id = await _discover_current_expansion_id(client)

    # 获取完整的资料片信息（包含名称）
    gql = f"""
        worldData {{
            expansion(id: {exp_id}) {{
                name
                zones {{
                    id
                    name
                    encounters {{
                        id
                        name
                    }}
                }}
            }}
        }}
    """
    data = await client.query(gql)
    expansion_data = data.get("worldData", {}).get("expansion", {})
    expansion_name = expansion_data.get("name", f"Expansion {exp_id}")
    raw_zones = expansion_data.get("zones") or []

    # 构建模型
    zones: list[Zone] = []
    for rz in raw_zones:
        encounters = [
            Encounter(id=e["id"], name=e["name"])
            for e in (rz.get("encounters") or [])
        ]
        zones.append(
            Zone(id=rz["id"], name=rz["name"], encounters=encounters)
        )

    # 过滤
    if content_type == "raid":
        zones = _filter_raid_zones(zones)
    elif content_type == "mythic_plus":
        zones = _filter_dungeon_zones(zones)

    response = EncountersResponse(
        expansion=expansion_name, zones=zones
    )

    # 写入缓存
    cache_set(cache_key, response.model_dump())
    logger.info(
        "get_encounters 完成: %d 个区域, %d 个遭遇",
        len(zones),
        sum(len(z.encounters) for z in zones),
    )
    return response


# ============================================================
# 过滤辅助
# ============================================================


def _filter_raid_zones(zones: list[Zone]) -> list[Zone]:
    """
    过滤出团本区域。

    启发式规则: 团本通常有 3+ 个 Boss。
    """
    return [z for z in zones if len(z.encounters) >= 3]


def _filter_dungeon_zones(zones: list[Zone]) -> list[Zone]:
    """
    过滤出地下城区域。

    启发式规则: 地下城通常 1-2 个 Boss。
    """
    return [z for z in zones if 0 < len(z.encounters) < 3]
