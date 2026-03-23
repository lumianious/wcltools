"""
get_boss_cast_timeline 工具 — 查询 boss 技能施法时间线。

从 WCL 报告中获取指定战斗的敌方施法事件，返回 boss 技能时间线。
Claude 可用此工具与玩家 CD 数据交叉分析（如爆发对齐小怪出现）。

WCL 数据流:
  1. report.fights → encounterID, startTime, endTime
  2. report.events(hostilityType: Enemies, dataType: Casts) → 敌方施法事件

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

from src.data import get_boss
from src.models import BossCastEvent, BossCastTimelineResponse
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"warcraftlogs\.com/reports/([A-Za-z0-9]+)")


def _extract_report_code(report: str) -> str:
    """从 URL 或纯 code 中提取 report code。"""
    m = _URL_PATTERN.search(report)
    if m:
        return m.group(1)
    return report.strip()


# ============================================================
# WCL 查询
# ============================================================


async def _query_fight_info(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> Optional[dict]:
    """查询指定战斗的基本信息。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights(fightIDs: [{fight_id}]) {{
                    id
                    startTime
                    endTime
                    encounterID
                    name
                }}
            }}
        }}
    """
    data = await client.query(gql)
    fights = (
        data.get("reportData", {})
        .get("report", {})
        .get("fights", [])
    )
    return fights[0] if fights else None


async def _query_enemy_cast_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    ability_ids: Optional[list[int]] = None,
) -> list[dict]:
    """查询敌方施法事件。

    使用 hostilityType: Enemies 获取 boss 施法。
    可选按 abilityID 过滤特定技能。
    """
    all_events: list[dict] = []

    if ability_ids:
        # 按 ability 分别查询，减少数据量
        for ability_id in ability_ids:
            next_ts: int | None = start_time
            while next_ts is not None:
                gql = f"""
                    reportData {{
                        report(code: "{report_code}") {{
                            events(
                                startTime: {next_ts}
                                endTime: {end_time}
                                hostilityType: Enemies
                                dataType: Casts
                                abilityID: {ability_id}
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
    else:
        # 查询所有敌方施法
        next_ts_all: int | None = start_time
        while next_ts_all is not None:
            gql = f"""
                reportData {{
                    report(code: "{report_code}") {{
                        events(
                            startTime: {next_ts_all}
                            endTime: {end_time}
                            hostilityType: Enemies
                            dataType: Casts
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
            next_ts_all = events_block.get("nextPageTimestamp")

    return all_events


# ============================================================
# 公开接口
# ============================================================


async def get_boss_cast_timeline(
    client: WCLClient,
    report: str,
    fight_id: int,
    spell_ids: Optional[list[int]] = None,
) -> BossCastTimelineResponse:
    """
    查询指定战斗中的 boss 技能施法时间线。

    如果提供 spell_ids，只查询指定技能；否则使用 bosses.json
    中该 boss 的所有已知技能。如果 boss 未收录，查询全部敌方施法。

    Args:
        client: WCL API 客户端
        report: Report code 或完整 WCL URL
        fight_id: 战斗 ID
        spell_ids: 可选，要查询的特定技能 ID 列表
    """
    report_code = _extract_report_code(report)

    # Step 1: 战斗信息
    fight_info = await _query_fight_info(client, report_code, fight_id)
    if not fight_info:
        raise ValueError(f"未找到战斗 fight_id={fight_id} in report {report_code}")

    start_time = fight_info.get("startTime", 0)
    end_time = fight_info.get("endTime", 0)
    encounter_id = fight_info.get("encounterID", 0)
    encounter_name = fight_info.get("name", "")
    fight_duration = (end_time - start_time) / 1000.0

    # Step 2: 确定要查询的技能
    ability_ids = spell_ids
    if not ability_ids and encounter_id:
        boss = get_boss(encounter_id)
        if boss:
            ability_ids = [
                s["spell_id"] for s in boss.get("spells", [])
                if s.get("spell_id")
            ]

    # Step 3: 查询敌方事件
    raw_events = await _query_enemy_cast_events(
        client, report_code, start_time, end_time, ability_ids,
    )

    # Step 4: 构建技能名映射（优先用 bosses.json，退化到 WCL 数据）
    spell_name_map: dict[int, str] = {}
    if encounter_id:
        boss = get_boss(encounter_id)
        if boss:
            for s in boss.get("spells", []):
                spell_name_map[s["spell_id"]] = s["name"]

    # Step 5: 解析事件
    events: list[BossCastEvent] = []
    counts: dict[str, int] = defaultdict(int)

    for evt in raw_events:
        if evt.get("type") != "cast":
            continue
        sid = evt.get("abilityGameID")
        if not sid:
            continue
        ts_sec = (evt.get("timestamp", 0) - start_time) / 1000.0
        name = spell_name_map.get(sid, evt.get("ability", {}).get("name", f"Spell {sid}"))

        events.append(BossCastEvent(
            spell_id=sid,
            spell_name=name,
            timestamp_sec=round(ts_sec, 1),
        ))
        counts[name] += 1

    # 按时间排序
    events.sort(key=lambda e: e.timestamp_sec)

    return BossCastTimelineResponse(
        report_code=report_code,
        fight_id=fight_id,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        fight_duration=round(fight_duration, 1),
        events=events,
        spell_summary=dict(counts),
    )
