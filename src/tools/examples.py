"""
get_example_logs 工具 — 返回指定专精/Boss 的优秀日志 URL。

从 WCL characterRankings 获取 Top N 玩家的报告链接，
过滤匿名玩家，构建完整的 WCL 报告 URL。

缓存 6 小时。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
from typing import Any

# ============================================================
# 第三方库
# ============================================================
from pydantic import BaseModel, Field

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.tools.builds import DIFFICULTY_MAP, SPEC_MAPPING
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时
WCL_REPORT_BASE = "https://www.warcraftlogs.com/reports"


# ============================================================
# 响应模型
# ============================================================
class ExampleLog(BaseModel):
    """单条示范日志。"""
    url: str = Field(description="完整 WCL 报告 URL（含 fight 锚点）")
    report_code: str
    fight_id: int
    player_name: str
    dps: float
    fight_duration: float = Field(description="战斗时长（秒）")
    rank: int


class ExampleLogsResponse(BaseModel):
    """示范日志合集。"""
    spec: str
    encounter_id: int
    encounter_name: str
    difficulty: str
    logs: list[ExampleLog] = []


# ============================================================
# Spec 解析
# ============================================================
def _parse_spec(spec: str) -> tuple[str, str]:
    """将 spec slug 解析为 (className, specName)。"""
    key = spec.lower().strip()
    if key in SPEC_MAPPING:
        return SPEC_MAPPING[key]
    raise ValueError(
        f"无法解析 spec: '{spec}'。请使用格式如 'frost-death-knight'"
    )


# ============================================================
# 公开接口
# ============================================================
async def get_example_logs(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
    count: int = 5,
) -> ExampleLogsResponse:
    """
    获取指定专精在指定 Boss 上的优秀日志 URL。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "frost-death-knight"
        encounter_id: Boss 遭遇 ID
        difficulty: 难度 — "normal" / "heroic" / "mythic"
        count: 返回日志数量（3-5）

    Returns:
        ExampleLogsResponse 包含日志 URL 列表
    """
    difficulty = difficulty or "heroic"
    count = max(3, min(count, 5))
    cache_key = f"examples:{spec}:{encounter_id}:{difficulty}:{count}"

    # 尝试缓存
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_example_logs 缓存命中")
        return ExampleLogsResponse(**cached)

    # 解析 spec
    class_name, spec_name = _parse_spec(spec)
    diff_id = DIFFICULTY_MAP.get(difficulty, 4)

    # 查询 WCL — 多取一些以应对匿名玩家过滤
    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "{class_name}"
                    specName: "{spec_name}"
                    metric: dps
                    difficulty: {diff_id}
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql)
    encounter_data = data.get("worldData", {}).get("encounter", {})
    encounter_name = encounter_data.get("name", "")
    rankings_data = encounter_data.get("characterRankings", {})
    rankings = rankings_data.get("rankings", [])

    # 提取日志，过滤匿名玩家
    logs: list[ExampleLog] = []
    for i, entry in enumerate(rankings):
        if len(logs) >= count:
            break

        player_name = entry.get("name", "")
        # 过滤匿名玩家
        if not player_name or player_name.lower() == "anonymous":
            continue

        report = entry.get("report", {})
        report_code = report.get("code", "")
        fight_id = report.get("fightID", 0)
        if not report_code:
            continue

        # duration: WCL 返回毫秒，转换为秒
        duration_ms = entry.get("duration", 0)
        duration_sec = round(duration_ms / 1000.0, 1)

        url = f"{WCL_REPORT_BASE}/{report_code}#fight={fight_id}"

        logs.append(ExampleLog(
            url=url,
            report_code=report_code,
            fight_id=fight_id,
            player_name=player_name,
            dps=round(entry.get("amount", 0.0), 1),
            fight_duration=duration_sec,
            rank=i + 1,
        ))

    logger.info(
        "get_example_logs: %s on %s (%s), 返回 %d 条日志",
        spec, encounter_name, difficulty, len(logs),
    )

    response = ExampleLogsResponse(
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        difficulty=difficulty,
        logs=logs,
    )

    # 写入缓存
    cache_set(cache_key, response.model_dump())
    return response
