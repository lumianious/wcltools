"""
get_buff_timeline 工具 — 提取玩家在指定战斗中的 Buff 事件时间线。

从 WCL 报告中获取指定玩家的 Buff apply/remove/stack 事件，
计算每个 Buff 的覆盖率和平均层数。

WCL 数据流:
  1. report.fights → startTime, endTime
  2. report.masterData → actors → sourceID
  3. report.events(dataType: Buffs) → Buff 事件（分页）

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import asyncio
import logging
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.models import BuffEvent, BuffSummary, BuffTimelineResponse
from src.tools._wcl_helpers import (
    extract_report_code,
    find_actor_id_ci,
    query_fight_info_full,
)
from src.tools.rotation import _query_master_data
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)


# ============================================================
# WCL 查询: Buff 事件（分页）
# ============================================================


async def _query_buff_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """分页查询指定玩家的 Buff 事件（apply/remove/stack）。"""
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
                        dataType: Buffs
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


async def _query_debuff_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """分页查询指定玩家施加的 Debuff 事件（DoT 追踪）。"""
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
                        dataType: Debuffs
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
# 常量 — Buff/Debuff 事件类型
# ============================================================

# 统一的事件类型集合（包含 buff 和 debuff 变体）
_VALID_EVENT_TYPES = {
    "applybuff", "removebuff", "applybuffstack", "removebuffstack", "refreshbuff",
    "applydebuff", "removedebuff", "applydebuffstack", "removedebuffstack", "refreshdebuff",
}

# 归一化映射: debuff 事件类型 → 等价的 buff 事件类型
_NORMALIZE_TYPE: dict[str, str] = {
    "applydebuff": "applybuff",
    "removedebuff": "removebuff",
    "applydebuffstack": "applybuffstack",
    "removedebuffstack": "removebuffstack",
    "refreshdebuff": "refreshbuff",
}


# ============================================================
# Buff 事件处理 — 计算覆盖率和平均层数
# ============================================================


def _compute_buff_uptime(
    evts: list[dict],
    fight_start: int,
    fight_end: int,
    fight_duration_ms: int,
    buff_id: int,
    buff_name: str,
) -> BuffSummary:
    """计算单个 Buff 的覆盖率、平均层数，并构建事件模型列表。"""
    model_events: list[BuffEvent] = []
    apply_count = 0
    total_uptime_ms = 0
    last_apply_ts: Optional[int] = None
    stack_samples: list[int] = []
    current_stacks = 0

    for evt in evts:
        ts = evt.get("timestamp", 0)
        raw_evt_type = evt.get("type", "")
        evt_type = _NORMALIZE_TYPE.get(raw_evt_type, raw_evt_type)
        stacks = evt.get("stack", 0)

        apply_count, total_uptime_ms, last_apply_ts, current_stacks = (
            _update_buff_state(
                evt_type, ts, stacks,
                apply_count, total_uptime_ms, last_apply_ts,
                current_stacks, stack_samples,
            )
        )
        model_events.append(BuffEvent(
            buff_id=buff_id, buff_name=buff_name,
            event_type=raw_evt_type,
            timestamp_sec=round((ts - fight_start) / 1000.0, 3),
            stacks=current_stacks,
        ))

    # Buff 在战斗结束时仍然存在（未 remove）
    if last_apply_ts is not None:
        total_uptime_ms += fight_end - last_apply_ts

    uptime_pct = round(min((total_uptime_ms / fight_duration_ms) * 100.0, 100.0), 1)
    avg_stacks = (
        round(sum(stack_samples) / len(stack_samples), 2) if stack_samples else 0.0
    )
    return BuffSummary(
        buff_id=buff_id, buff_name=buff_name,
        uptime_pct=uptime_pct, avg_stacks=avg_stacks,
        apply_count=apply_count, events=model_events,
    )


def _update_buff_state(
    evt_type: str,
    ts: int,
    stacks: int,
    apply_count: int,
    total_uptime_ms: int,
    last_apply_ts: Optional[int],
    current_stacks: int,
    stack_samples: list[int],
) -> tuple[int, int, Optional[int], int]:
    """根据事件类型更新 Buff 状态，返回更新后的状态元组。"""
    if evt_type == "applybuff":
        last_apply_ts = ts
        apply_count += 1
        current_stacks = max(1, stacks)
        stack_samples.append(current_stacks)
    elif evt_type == "removebuff":
        if last_apply_ts is not None:
            total_uptime_ms += ts - last_apply_ts
            last_apply_ts = None
        current_stacks = 0
    elif evt_type == "refreshbuff":
        # refresh = 计入当前区间覆盖率，重新开始计时
        if last_apply_ts is not None:
            total_uptime_ms += ts - last_apply_ts
        apply_count += 1
        last_apply_ts = ts
        current_stacks = max(1, stacks) if stacks > 0 else max(1, current_stacks)
    elif evt_type == "applybuffstack":
        current_stacks = stacks if stacks > 0 else current_stacks + 1
        stack_samples.append(current_stacks)
    elif evt_type == "removebuffstack":
        current_stacks = stacks if stacks > 0 else max(0, current_stacks - 1)
        stack_samples.append(current_stacks)

    return apply_count, total_uptime_ms, last_apply_ts, current_stacks


def _process_single_buff(
    buff_id: int,
    evts: list[dict],
    fight_start: int,
    fight_end: int,
    fight_duration_ms: int,
    buff_names: dict[int, str],
) -> BuffSummary:
    """处理单个 Buff 的事件列表，排序后计算覆盖率和层数。"""
    evts.sort(key=lambda e: e.get("timestamp", 0))
    return _compute_buff_uptime(
        evts, fight_start, fight_end, fight_duration_ms,
        buff_id, buff_names[buff_id],
    )


def _process_buff_events(
    events: list[dict],
    fight_start: int,
    fight_end: int,
    buff_ids: Optional[list[int]],
    ability_map: dict[int, str],
) -> list[BuffSummary]:
    """
    处理原始 Buff/Debuff 事件，计算每个 Buff 的覆盖率和平均层数。

    事件类型:
      Buff: applybuff, removebuff, applybuffstack, removebuffstack, refreshbuff
      Debuff: applydebuff, removedebuff, applydebuffstack, removedebuffstack, refreshdebuff
    """
    fight_duration_ms = fight_end - fight_start
    if fight_duration_ms <= 0:
        return []

    # 按 Buff ID 分组事件
    buff_events: dict[int, list[dict]] = defaultdict(list)
    buff_names: dict[int, str] = {}

    for evt in events:
        evt_type = evt.get("type", "")
        if evt_type not in _VALID_EVENT_TYPES:
            continue

        buff_id = evt.get("abilityGameID")
        if not buff_id:
            continue

        # 过滤指定的 buff_ids
        if buff_ids is not None and buff_id not in buff_ids:
            continue

        buff_events[buff_id].append(evt)
        if buff_id not in buff_names:
            buff_names[buff_id] = ability_map.get(buff_id, f"Buff {buff_id}")

    # 处理每个 Buff
    summaries = [
        _process_single_buff(
            bid, evts, fight_start, fight_end, fight_duration_ms, buff_names,
        )
        for bid, evts in buff_events.items()
    ]

    # 按覆盖率降序排列
    summaries.sort(key=lambda s: s.uptime_pct, reverse=True)
    return summaries


# ============================================================
# 公开接口
# ============================================================


async def _resolve_player_and_fight(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    player: str,
) -> tuple[dict[str, Any], int, list[dict], dict[int, str]]:
    """
    解析战斗信息和玩家 ID。

    返回 (fight_info, source_id, actors, ability_map)。
    """
    # 战斗信息
    fight_info = await query_fight_info_full(client, report_code, fight_id)
    if not fight_info:
        raise ValueError(f"未找到战斗 fight_id={fight_id} in report {report_code}")

    fight_start = fight_info.get("startTime", 0)
    fight_end = fight_info.get("endTime", 0)
    if not fight_start or not fight_end:
        raise ValueError("战斗缺少有效的起止时间")

    # masterData
    actors, ability_map = await _query_master_data(client, report_code)
    source_id = find_actor_id_ci(actors, player)
    if source_id is None:
        raise ValueError(f"未找到玩家 '{player}' in report {report_code}")

    return fight_info, source_id, actors, ability_map


def _compute_query_range(
    fight_start: int,
    fight_end: int,
    time_start: float,
    time_end: float,
) -> tuple[int, int]:
    """根据用户指定的相对时间窗口计算绝对查询范围（毫秒）。"""
    query_start = fight_start + int(time_start * 1000) if time_start > 0 else fight_start
    query_end = fight_start + int(time_end * 1000) if time_end > 0 else fight_end
    query_start = max(query_start, fight_start)
    query_end = min(query_end, fight_end)
    return query_start, query_end


def _trim_buff_events(
    all_buffs: list[BuffSummary],
    buff_ids: Optional[list[int]],
) -> None:
    """
    按 buff_ids 过滤裁剪事件详情（原地修改）。

    指定 buff_ids 时仅保留匹配的事件详情，其余清空。
    未指定时仅保留前 15 个 Buff 的事件详情以控制体积。
    """
    if buff_ids is not None:
        buff_ids_set = set(buff_ids)
        for b in all_buffs:
            if b.buff_id not in buff_ids_set:
                b.events = []
    else:
        # 未指定 buff_ids: 只保留前 15 个 buff 的事件详情
        # 其余清空事件以控制响应体积（避免 180KB+）
        for b in all_buffs[15:]:
            b.events = []


async def get_buff_timeline(
    client: WCLClient,
    report: str,
    fight_id: int,
    player: str,
    buff_ids: Optional[list[int]] = None,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> BuffTimelineResponse:
    """提取玩家在指定战斗中的 Buff 事件时间线。"""
    report_code = extract_report_code(report)
    logger.info(
        "get_buff_timeline: %s in %s fight=%d [%.1f-%.1f]",
        player, report_code, fight_id, time_start, time_end,
    )

    # ---- Step 1-2: 战斗信息 + masterData + 玩家查找 ----
    fight_info, source_id, _actors, ability_map = await _resolve_player_and_fight(
        client, report_code, fight_id, player,
    )
    fight_start, fight_end = fight_info["startTime"], fight_info["endTime"]
    fight_duration = (fight_end - fight_start) / 1000.0

    # ---- Step 3: 计算查询时间范围 ----
    query_start, query_end = _compute_query_range(
        fight_start, fight_end, time_start, time_end,
    )

    # ---- Step 4: 查询 Buff + Debuff 事件 ----
    buff_events_list, debuff_events_list = await asyncio.gather(
        _query_buff_events(client, report_code, query_start, query_end, source_id),
        _query_debuff_events(client, report_code, query_start, query_end, source_id),
    )

    # ---- Step 5: 处理事件并裁剪 ----
    all_buffs = _process_buff_events(
        buff_events_list + debuff_events_list,
        query_start, query_end, None, ability_map,
    )
    _trim_buff_events(all_buffs, buff_ids)

    return BuffTimelineResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        fight_duration=round(fight_duration, 1),
        time_start=round(time_start if time_start > 0 else 0.0, 1),
        time_end=round(time_end if time_end > 0 else fight_duration, 1),
        buffs=all_buffs,
    )
