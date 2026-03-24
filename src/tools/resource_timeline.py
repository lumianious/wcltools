"""
get_resource_timeline 工具 — 提取玩家在指定战斗中的资源变化时间线。

从 WCL 报告中通过 dataType: Resources 查询 resourcechange 事件，
追踪资源值变化和溢出（waste > 0）。

WCL 数据流:
  1. report.fights → startTime, endTime
  2. report.masterData → actors → sourceID
  3. report.events(dataType: Resources) → resourcechange 事件

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from collections import Counter
from typing import Optional

# ============================================================
# 本地模块
# ============================================================
from src.models import ResourcePoint, ResourceTimelineResponse
from src.tools._wcl_helpers import (
    extract_report_code,
    find_actor_id_ci,
    query_fight_info_full,
)
from src.tools.rotation import _query_master_data
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)


# ============================================================
# WCL 查询: 资源事件（分页）
# ============================================================


async def _query_resource_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """分页查询指定玩家的资源变化事件。"""
    all_events: list[dict] = []
    next_ts: Optional[int] = start_time

    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(
                        startTime: {next_ts}
                        endTime: {end_time}
                        sourceID: {source_id}
                        dataType: Resources
                        limit: 10000
                    ) {{
                        data
                        nextPageTimestamp
                    }}
                }}
            }}
        """
        data = await client.query(gql)
        report = data.get("reportData", {}).get("report", {})
        events_block = report.get("events", {})
        page_data = events_block.get("data", [])
        all_events.extend(page_data)
        next_ts = events_block.get("nextPageTimestamp")

    return all_events


# ============================================================
# 内部辅助: 解析玩家与战斗
# ============================================================


async def _resolve_player_and_fight(
    client: WCLClient, report_code: str, fight_id: int, player: str,
) -> tuple[int, int, int, float, list, dict]:
    """查询战斗信息 + masterData，返回 (source_id, fight_start, fight_end, duration, actors, ability_map)。"""
    # 战斗信息
    fight_info = await query_fight_info_full(client, report_code, fight_id)
    if not fight_info:
        raise ValueError(f"未找到战斗 fight_id={fight_id} in report {report_code}")

    fight_start = fight_info.get("startTime", 0)
    fight_end = fight_info.get("endTime", 0)
    if not fight_start or not fight_end:
        raise ValueError("战斗缺少有效的起止时间")

    duration = (fight_end - fight_start) / 1000.0

    # masterData — 角色列表 + 技能映射
    actors, ability_map = await _query_master_data(client, report_code)
    source_id = find_actor_id_ci(actors, player)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player}' in report {report_code}")

    return source_id, fight_start, fight_end, duration, actors, ability_map


# ============================================================
# 内部辅助: 自动检测资源类型
# ============================================================


def _detect_resource_type(
    events: list[dict], resource_type_lower: str,
) -> tuple[int, str] | None:
    """根据事件统计检测资源类型，返回 (type_id, display_name) 或 None（无事件）。"""
    type_counts = Counter(
        e.get("resourceChangeType")
        for e in events
        if e.get("type") == "resourcechange"
    )
    if not type_counts:
        return None

    target_type_id = type_counts.most_common(1)[0][0]

    if resource_type_lower == "auto":
        detected_name = f"resource_type_{target_type_id}"
    else:
        # 由于 WCL type ID 不稳定，直接用最常见的；保留用户指定的名称
        detected_name = resource_type_lower

    return target_type_id, detected_name


# ============================================================
# 内部辅助: 解析事件为 ResourcePoint 列表
# ============================================================


def _extract_resource_points(
    events: list[dict],
    target_type_id: int,
    fight_start: int,
    ability_map: dict,
) -> tuple[list[ResourcePoint], int]:
    """将原始事件解析为 ResourcePoint 列表，返回 (points, overflow_count)。"""
    points: list[ResourcePoint] = []
    overflow_count = 0

    for evt in events:
        if evt.get("type") != "resourcechange":
            continue
        if evt.get("resourceChangeType") != target_type_id:
            continue

        ts = evt.get("timestamp")
        if ts is None:
            continue

        change = evt.get("resourceChange", 0)
        waste = evt.get("waste", 0)
        max_val = evt.get("maxResourceAmount", 0)
        spell_id = evt.get("abilityGameID", 0)
        spell_name = ability_map.get(spell_id, f"Spell {spell_id}")
        timestamp_sec = round((ts - fight_start) / 1000.0, 3)

        # WCL 内部以 ×10 存储某些资源值 — 检测并归一化
        if max_val > 200 and max_val % 10 == 0:
            max_val = max_val // 10

        is_overflow = waste > 0
        if is_overflow:
            overflow_count += 1

        points.append(ResourcePoint(
            timestamp_sec=timestamp_sec,
            value=change,
            max_value=max_val,
            spell_name=spell_name,
            is_overflow=is_overflow,
        ))

    return points, overflow_count


# ============================================================
# 公开接口
# ============================================================


async def get_resource_timeline(
    client: WCLClient,
    report: str,
    fight_id: int,
    player: str,
    resource_type: str = "auto",
) -> ResourceTimelineResponse:
    """
    提取玩家在指定战斗中的资源变化时间线。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        fight_id: 战斗 ID
        player: 角色名（大小写不敏感）
        resource_type: 资源类型。"auto" 自动选择该玩家唯一/主要资源类型。
                       也可指定如 "astral_power", "rage" 等（用于筛选）。

    Returns:
        ResourceTimelineResponse 资源时间线
    """
    report_code = extract_report_code(report)
    resource_type_lower = resource_type.lower().strip()

    logger.info(
        "get_resource_timeline: %s in %s fight=%d resource=%s",
        player, report_code, fight_id, resource_type_lower,
    )

    # Step 1-2: 战斗信息 + masterData + 角色查找
    source_id, fight_start, fight_end, duration, _, ability_map = (
        await _resolve_player_and_fight(client, report_code, fight_id, player)
    )

    # Step 3: 查询资源事件
    events = await _query_resource_events(
        client, report_code, fight_start, fight_end, source_id,
    )

    # Step 4: 自动检测资源类型
    detected = _detect_resource_type(events, resource_type_lower)
    if detected is None:
        return ResourceTimelineResponse(
            report_code=report_code, fight_id=fight_id,
            player_name=player, resource_type=resource_type_lower,
            fight_duration=round(duration, 1),
        )
    target_type_id, detected_name = detected

    # Step 5: 提取资源值
    points, overflow_count = _extract_resource_points(
        events, target_type_id, fight_start, ability_map,
    )

    total_points = len(points)
    overflow_pct = round(overflow_count / total_points * 100.0, 1) if total_points else 0.0

    return ResourceTimelineResponse(
        report_code=report_code, fight_id=fight_id,
        player_name=player, resource_type=detected_name,
        fight_duration=round(duration, 1),
        total_points=total_points, overflow_count=overflow_count,
        overflow_pct=overflow_pct, points=points,
    )
