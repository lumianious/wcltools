"""
get_cast_sequence 工具 — 提取玩家在指定战斗中的施法序列。

从 WCL 报告中获取指定玩家的施法事件，按时间排序返回。
支持时间范围过滤（相对于战斗开始的秒数）。

WCL 数据流:
  1. report.fights → startTime, endTime
  2. report.masterData → actors + ability map → sourceID
  3. report.events(Casts) → 施法事件（分页）

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from typing import Any

# ============================================================
# 本地模块
# ============================================================
from src.models import CastEvent, CastSequenceResponse
from src.tools.analyze import _extract_report_code, _find_actor_id_ci, _query_fight_info_full
from src.tools.rotation import _query_cast_events, _query_master_data
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)


# ============================================================
# 公开接口
# ============================================================


async def get_cast_sequence(
    client: WCLClient,
    report: str,
    fight_id: int,
    player: str,
    spec: str,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> CastSequenceResponse:
    """
    提取玩家在指定战斗中的施法序列。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        fight_id: 战斗 ID
        player: 角色名（大小写不敏感）
        spec: 专精 slug（用于返回值标注）
        time_start: 起始时间（相对于战斗开始，秒）。0 表示从头开始。
        time_end: 结束时间（相对于战斗开始，秒）。0 表示到战斗结束。

    Returns:
        CastSequenceResponse 施法序列
    """
    report_code = _extract_report_code(report)

    logger.info(
        "get_cast_sequence: %s in %s fight=%d [%.1f-%.1f]",
        player, report_code, fight_id, time_start, time_end,
    )

    # ---- Step 1: 战斗信息 ----
    fight_info = await _query_fight_info_full(client, report_code, fight_id)
    if not fight_info:
        raise ValueError(f"未找到战斗 fight_id={fight_id} in report {report_code}")

    fight_start = fight_info.get("startTime", 0)
    fight_end = fight_info.get("endTime", 0)
    if not fight_start or not fight_end:
        raise ValueError("战斗缺少有效的起止时间")

    fight_duration = (fight_end - fight_start) / 1000.0

    # ---- Step 2: masterData ----
    actors, ability_map = await _query_master_data(client, report_code)
    source_id = _find_actor_id_ci(actors, player)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player}' in report {report_code}")

    # ---- Step 3: 计算查询时间范围 ----
    query_start = fight_start + int(time_start * 1000) if time_start > 0 else fight_start
    query_end = fight_start + int(time_end * 1000) if time_end > 0 else fight_end

    # 边界保护
    query_start = max(query_start, fight_start)
    query_end = min(query_end, fight_end)

    # ---- Step 4: 查询施法事件 ----
    events = await _query_cast_events(
        client, report_code, query_start, query_end, source_id
    )

    # ---- Step 5: 转换为 CastEvent 列表 ----
    casts: list[CastEvent] = []
    for evt in events:
        if evt.get("type") != "cast":
            continue
        spell_id = evt.get("abilityGameID")
        if not spell_id:
            continue
        ts = evt.get("timestamp")
        if ts is None:
            continue

        spell_name = ability_map.get(spell_id, f"Spell {spell_id}")
        timestamp_sec = round((ts - fight_start) / 1000.0, 3)

        # 提取 classResources 中的资源值（如星界能量）
        resource_amount = None
        resource_max = None
        class_resources = evt.get("classResources")
        if class_resources:
            # 取第一个资源条目（通常是主要资源）
            res = class_resources[0]
            resource_amount = float(res.get("amount", 0))
            resource_max = float(res.get("max", 0)) or None

        casts.append(CastEvent(
            spell_id=spell_id,
            spell_name=spell_name,
            timestamp_sec=timestamp_sec,
            resource_amount=resource_amount,
            resource_max=resource_max,
        ))

    # 按时间排序
    casts.sort(key=lambda c: c.timestamp_sec)

    # 实际返回的时间范围
    actual_start = time_start if time_start > 0 else 0.0
    actual_end = time_end if time_end > 0 else fight_duration

    return CastSequenceResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        spec=spec,
        fight_duration=round(fight_duration, 1),
        time_start=round(actual_start, 1),
        time_end=round(actual_end, 1),
        total_casts=len(casts),
        casts=casts,
    )
