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
import logging
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.models import BuffEvent, BuffSummary, BuffTimelineResponse
from src.tools.analyze import _extract_report_code, _find_actor_id_ci, _query_fight_info_full
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
# Buff 事件处理 — 计算覆盖率和平均层数
# ============================================================


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
    summaries: list[BuffSummary] = []
    for buff_id, evts in buff_events.items():
        evts.sort(key=lambda e: e.get("timestamp", 0))

        model_events: list[BuffEvent] = []
        apply_count = 0
        total_uptime_ms = 0
        last_apply_ts: Optional[int] = None
        stack_samples: list[int] = []
        current_stacks = 0

        for evt in evts:
            ts = evt.get("timestamp", 0)
            raw_evt_type = evt.get("type", "")
            # 归一化 debuff 事件类型 → 等价 buff 事件类型
            evt_type = _NORMALIZE_TYPE.get(raw_evt_type, raw_evt_type)
            timestamp_sec = round((ts - fight_start) / 1000.0, 3)
            stacks = evt.get("stack", 0)

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

            model_events.append(BuffEvent(
                buff_id=buff_id,
                buff_name=buff_names[buff_id],
                event_type=raw_evt_type,  # 保留原始事件类型供调用者区分 buff/debuff
                timestamp_sec=timestamp_sec,
                stacks=current_stacks,
            ))

        # 如果 Buff 在战斗结束时仍然存在（未 remove）
        if last_apply_ts is not None:
            total_uptime_ms += fight_end - last_apply_ts

        uptime_pct = round(min((total_uptime_ms / fight_duration_ms) * 100.0, 100.0), 1)
        avg_stacks = round(sum(stack_samples) / len(stack_samples), 2) if stack_samples else 0.0

        summaries.append(BuffSummary(
            buff_id=buff_id,
            buff_name=buff_names[buff_id],
            uptime_pct=uptime_pct,
            avg_stacks=avg_stacks,
            apply_count=apply_count,
            events=model_events,
        ))

    # 按覆盖率降序排列
    summaries.sort(key=lambda s: s.uptime_pct, reverse=True)
    return summaries


# ============================================================
# 公开接口
# ============================================================


async def get_buff_timeline(
    client: WCLClient,
    report: str,
    fight_id: int,
    player: str,
    buff_ids: Optional[list[int]] = None,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> BuffTimelineResponse:
    """
    提取玩家在指定战斗中的 Buff 事件时间线。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        fight_id: 战斗 ID
        player: 角色名（大小写不敏感）
        buff_ids: 可选的 Buff spell ID 过滤列表。None 表示返回所有 Buff。
        time_start: 起始时间（相对于战斗开始，秒）。0 表示从头开始。
        time_end: 结束时间（相对于战斗开始，秒）。0 表示到战斗结束。

    Returns:
        BuffTimelineResponse Buff 时间线
    """
    report_code = _extract_report_code(report)

    logger.info(
        "get_buff_timeline: %s in %s fight=%d [%.1f-%.1f]",
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
    query_start = max(query_start, fight_start)
    query_end = min(query_end, fight_end)

    # ---- Step 4: 查询 Buff + Debuff 事件 ----
    import asyncio
    buff_events_task = _query_buff_events(
        client, report_code, query_start, query_end, source_id
    )
    debuff_events_task = _query_debuff_events(
        client, report_code, query_start, query_end, source_id
    )
    buff_events_list, debuff_events_list = await asyncio.gather(
        buff_events_task, debuff_events_task
    )
    # 合并 buff 和 debuff 事件
    all_events = buff_events_list + debuff_events_list

    # ---- Step 5: 处理事件 ----
    # 全部 buff 都处理（用于 summary），但事件详情只保留请求的 buff
    all_buffs = _process_buff_events(
        all_events, query_start, query_end, None, ability_map,  # None = 不过滤
    )

    # 如果指定了 buff_ids，仅保留这些 buff 的事件详情
    # 其他 buff 只保留 summary（清空 events 以减少响应体积）
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

    actual_start = time_start if time_start > 0 else 0.0
    actual_end = time_end if time_end > 0 else fight_duration

    return BuffTimelineResponse(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        fight_duration=round(fight_duration, 1),
        time_start=round(actual_start, 1),
        time_end=round(actual_end, 1),
        buffs=all_buffs,
    )
