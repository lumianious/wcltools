"""
WCL 共享工具函数 — 报告解析、玩家匹配、战斗查询。

多个工具模块共用的 WCL API 交互基础设施，
避免各工具重复实现相同的报告解析和查询逻辑。

公开接口:
  - extract_report_code(report) -> str
  - find_actor_id_ci(actors, player_name) -> Optional[int]
  - query_fight_info_full(client, report_code, fight_id) -> dict

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import re
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.tools.rotation import _find_actor_id
from src.wcl_client import WCLClient

# ============================================================
# 常量
# ============================================================
_URL_PATTERN = re.compile(
    r"warcraftlogs\.com/reports/([A-Za-z0-9]+)"
)


# ============================================================
# URL / Report Code 解析
# ============================================================


def extract_report_code(report: str) -> str:
    """
    从 report code 或完整 WCL URL 中提取 report code。

    支持:
      - "ABC123"
      - "https://www.warcraftlogs.com/reports/ABC123#fight=3"
    """
    report = report.strip()
    match = _URL_PATTERN.search(report)
    if match:
        return match.group(1)
    return report


# ============================================================
# 玩家名称匹配（大小写不敏感）
# ============================================================


def find_actor_id_ci(
    actors: list[dict], player_name: str
) -> Optional[int]:
    """
    大小写不敏感地在 actors 中查找玩家 sourceID。

    先尝试精确匹配（复用 rotation._find_actor_id），
    失败后降级为大小写不敏感匹配。
    """
    exact = _find_actor_id(actors, player_name)
    if exact is not None:
        return exact
    lower_name = player_name.lower()
    for actor in actors:
        if actor.get("name", "").lower() == lower_name:
            return actor.get("id")
    return None


# ============================================================
# WCL 查询: 战斗信息（含 encounterID）
# ============================================================


async def query_fight_info_full(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> dict[str, Any]:
    """
    查询指定战斗的完整信息（含 encounterID）。

    返回 {startTime, endTime, kill, encounterID, name}
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights(fightIDs: [{fight_id}]) {{
                    startTime
                    endTime
                    kill
                    encounterID
                    name
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    fights = report.get("fights", [])
    return fights[0] if fights else {}
