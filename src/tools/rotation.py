"""
get_rotation_profile 工具 — 聚合顶尖玩家的循环数据。

从 WCL characterRankings 获取 Top 5 玩家报告，
查询施法事件和 Buff 覆盖率，计算每个技能的施法次数、CPM、
百分位分布和 DPS 分布。

WCL 数据流:
  1. characterRankings → 排名 + DPS + report 信息
  2. report.fights → 战斗时长
  3. report.events(dataType: Casts) → 施法事件
  4. report.table(dataType: Buffs) → Buff 覆盖率

缓存 6 小时。

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
import statistics
from collections import defaultdict
from typing import Any, Optional

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.data import get_spell_name
from src.models import (
    BuffUptime,
    RotationProfileResponse,
    SpellStats,
)
from src.tools.builds import DIFFICULTY_MAP, SPEC_MAPPING
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时
TOP_N_PLAYERS = 5  # 采样玩家数
TOP_N_SPELLS = 15  # 返回的技能数量上限


# ============================================================
# Spec 解析
# ============================================================


def _parse_spec(spec: str) -> tuple[str, str]:
    """将 spec slug 解析为 (className, specName)。"""
    key = spec.lower().strip()
    if key in SPEC_MAPPING:
        return SPEC_MAPPING[key]
    parts = key.replace("-", " ").split()
    if len(parts) >= 2:
        spec_name = parts[0].capitalize()
        class_name = "".join(p.capitalize() for p in parts[1:])
        return class_name, spec_name
    raise ValueError(f"无法解析 spec: '{spec}'")


# ============================================================
# WCL 查询: 排行榜
# ============================================================


async def _query_rankings(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    difficulty: int,
) -> dict[str, Any]:
    """查询 WCL 排行榜数据（含战斗信息）。"""
    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "{class_name}"
                    specName: "{spec_name}"
                    metric: dps
                    difficulty: {difficulty}
                    includeCombatantInfo: true
                )
            }}
        }}
    """
    data = await client.query(gql)
    return data.get("worldData", {}).get("encounter", {})


# ============================================================
# WCL 查询: 战斗时长
# ============================================================


async def _query_fight_info(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> dict[str, Any]:
    """查询指定战斗的起止时间。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                fights(fightIDs: [{fight_id}]) {{
                    startTime
                    endTime
                    kill
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    fights = report.get("fights", [])
    return fights[0] if fights else {}


# ============================================================
# WCL 查询: masterData (获取 sourceID)
# ============================================================


async def _query_master_data(
    client: WCLClient, report_code: str
) -> tuple[list[dict], dict[int, str]]:
    """
    查询报告的 masterData，返回 (玩家列表, 技能名称映射)。

    abilities 包含该报告中所有技能的 gameID → name 映射，
    用于解析施法事件中的技能名称。
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                masterData(translate: true) {{
                    actors(type: "Player") {{
                        id
                        name
                        server
                        subType
                    }}
                    abilities {{
                        gameID
                        name
                    }}
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    master = report.get("masterData", {})
    actors = master.get("actors", [])
    # 构建 gameID → name 映射
    ability_map: dict[int, str] = {}
    for ab in master.get("abilities", []):
        gid = ab.get("gameID")
        name = ab.get("name")
        if gid and name:
            ability_map[gid] = name
    return actors, ability_map


def _find_actor_id(
    actors: list[dict], player_name: str
) -> Optional[int]:
    """在 actors 列表中按名字匹配玩家，返回 sourceID。"""
    for actor in actors:
        if actor.get("name") == player_name:
            return actor.get("id")
    return None


# ============================================================
# WCL 查询: 施法事件（分页）
# ============================================================


async def _query_cast_events(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """分页查询指定玩家在指定时间范围内的所有施法事件。"""
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
        next_ts = events_block.get("nextPageTimestamp")

    return all_events


# ============================================================
# WCL 查询: Buff 覆盖率
# ============================================================


async def _query_buff_table(
    client: WCLClient,
    report_code: str,
    start_time: int,
    end_time: int,
    source_id: int,
) -> list[dict]:
    """查询指定玩家的 Buff 覆盖率表格，返回 auras 列表。"""
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                table(
                    startTime: {start_time}
                    endTime: {end_time}
                    sourceID: {source_id}
                    dataType: Buffs
                )
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    table = report.get("table", {})
    # table 结构: { data: { auras: [...] } }
    table_data = table.get("data", {})
    return table_data.get("auras", [])


# ============================================================
# 单个玩家数据收集
# ============================================================


async def _collect_player_data(
    client: WCLClient,
    ranking: dict,
) -> Optional[dict]:
    """
    收集单个玩家的施法、Buff、战斗时长数据。

    返回:
      {
        "dps": float,
        "fight_duration": float (秒),
        "spell_counts": {spell_id: count},
        "buff_uptimes": [{name, abilityGameID, totalUptime}],
      }
    """
    report = ranking.get("report", {})
    report_code = report.get("code")
    fight_id = report.get("fightID")
    player_name = ranking.get("name", "")
    dps = ranking.get("amount", 0.0)

    if not report_code or not fight_id:
        return None

    # 查询战斗信息
    try:
        fight_info = await _query_fight_info(client, report_code, fight_id)
    except Exception as exc:
        logger.warning("战斗信息查询失败 %s: %s", report_code, exc)
        return None

    start_time = fight_info.get("startTime", 0)
    end_time = fight_info.get("endTime", 0)
    if not start_time or not end_time:
        return None

    fight_duration = (end_time - start_time) / 1000.0  # 毫秒转秒

    # 查询 masterData 获取 sourceID 和技能名称映射
    try:
        actors, ability_map = await _query_master_data(client, report_code)
    except Exception as exc:
        logger.warning("masterData 查询失败 %s: %s", report_code, exc)
        return None

    source_id = _find_actor_id(actors, player_name)
    if source_id is None:
        logger.debug("未找到玩家 %s 在报告 %s 中", player_name, report_code)
        return None

    # 查询施法事件
    try:
        events = await _query_cast_events(
            client, report_code, start_time, end_time, source_id
        )
    except Exception as exc:
        logger.warning("施法事件查询失败 %s/%s: %s", report_code, player_name, exc)
        return None

    # 统计每个技能的施法次数
    spell_counts: dict[int, int] = defaultdict(int)
    spell_names: dict[int, str] = {}
    for evt in events:
        # 只计算成功施法（type == "cast"）
        if evt.get("type") != "cast":
            continue
        spell_id = evt.get("abilityGameID")
        if spell_id:
            spell_counts[spell_id] += 1
            if spell_id not in spell_names:
                # 优先从 masterData.abilities 解析，退化到本地数据
                resolved = (
                    ability_map.get(spell_id)
                    or get_spell_name(spell_id)
                    or f"Spell {spell_id}"
                )
                spell_names[spell_id] = resolved

    # 查询 Buff 覆盖率
    try:
        auras = await _query_buff_table(
            client, report_code, start_time, end_time, source_id
        )
    except Exception as exc:
        logger.warning("Buff 查询失败 %s/%s: %s", report_code, player_name, exc)
        auras = []

    return {
        "dps": dps,
        "fight_duration": fight_duration,
        "spell_counts": dict(spell_counts),
        "spell_names": spell_names,
        "buff_uptimes": auras,
    }


# ============================================================
# 聚合计算
# ============================================================


def _aggregate_spell_stats(
    players_data: list[dict],
) -> list[SpellStats]:
    """
    聚合多个玩家的施法统计，计算中位数/百分位。

    返回按中位数施法次数降序排列的前 15 个技能。
    """
    # 收集所有出现过的技能 ID 和名称
    all_spell_ids: set[int] = set()
    spell_names: dict[int, str] = {}
    for pd in players_data:
        for sid in pd["spell_counts"]:
            all_spell_ids.add(sid)
        spell_names.update(pd["spell_names"])

    # 对每个技能计算跨玩家的统计
    results: list[SpellStats] = []
    for sid in all_spell_ids:
        # 收集每位玩家对该技能的施法次数（未施放 = 0）
        counts = [pd["spell_counts"].get(sid, 0) for pd in players_data]
        # 跳过大多数玩家都没用的技能（少于一半玩家使用）
        used_count = sum(1 for c in counts if c > 0)
        if used_count < len(players_data) / 2:
            continue

        sorted_counts = sorted(counts)
        n = len(sorted_counts)
        median_casts = statistics.median(sorted_counts)

        # CPM: 每位玩家的 CPM，然后取中位数
        cpms = []
        for pd in players_data:
            c = pd["spell_counts"].get(sid, 0)
            dur_min = pd["fight_duration"] / 60.0
            if dur_min > 0:
                cpms.append(c / dur_min)
        median_cpm = statistics.median(cpms) if cpms else 0.0

        # 百分位
        percentiles = {
            "p25": round(float(sorted_counts[max(0, n // 4 - 1)]), 1),
            "p50": round(float(statistics.median(sorted_counts)), 1),
            "p75": round(float(sorted_counts[min(n - 1, 3 * n // 4)]), 1),
        }

        name = spell_names.get(sid, f"Spell {sid}")
        results.append(SpellStats(
            name=name,
            spell_id=sid,
            total_casts=round(median_casts, 1),
            cpm=round(median_cpm, 2),
            percentiles=percentiles,
        ))

    # 按中位数施法次数降序排列，取前 15
    results.sort(key=lambda s: s.total_casts, reverse=True)
    return results[:TOP_N_SPELLS]


def _aggregate_buff_uptimes(
    players_data: list[dict],
) -> list[BuffUptime]:
    """
    聚合多个玩家的 Buff 覆盖率。

    WCL aura 结构: {name, abilityGameID, totalUptime (ms), ...}
    覆盖率 = totalUptime / fight_duration * 100
    """
    # 收集所有 Buff ID → 每位玩家的覆盖率
    buff_uptimes: dict[int, list[float]] = defaultdict(list)
    buff_names: dict[int, str] = {}

    for pd in players_data:
        fight_dur_ms = pd["fight_duration"] * 1000.0
        if fight_dur_ms <= 0:
            continue

        # 记录该玩家拥有的 buff ID
        player_buff_ids: set[int] = set()
        for aura in pd["buff_uptimes"]:
            # WCL buff table 使用 "guid" 而非 "abilityGameID"
            spell_id = aura.get("guid") or aura.get("abilityGameID")
            if not spell_id:
                continue
            player_buff_ids.add(spell_id)
            total_uptime = aura.get("totalUptime", 0)
            uptime_pct = (total_uptime / fight_dur_ms) * 100.0
            buff_uptimes[spell_id].append(min(uptime_pct, 100.0))
            if spell_id not in buff_names:
                buff_names[spell_id] = aura.get("name", f"Buff {spell_id}")

    # 过滤: 只保留大多数玩家都有的 buff
    threshold = len(players_data) / 2
    results: list[BuffUptime] = []
    for sid, uptimes in buff_uptimes.items():
        if len(uptimes) < threshold:
            continue
        median_pct = statistics.median(uptimes)
        results.append(BuffUptime(
            name=buff_names.get(sid, f"Buff {sid}"),
            spell_id=sid,
            uptime_pct=round(median_pct, 1),
        ))

    results.sort(key=lambda b: b.uptime_pct, reverse=True)
    return results


def _aggregate_dps(
    players_data: list[dict],
) -> tuple[float, float, float]:
    """计算 DPS 的中位数、P25、P75。"""
    dps_values = sorted(pd["dps"] for pd in players_data)
    if not dps_values:
        return 0.0, 0.0, 0.0
    n = len(dps_values)
    return (
        round(statistics.median(dps_values), 1),
        round(float(dps_values[max(0, n // 4 - 1)]), 1),
        round(float(dps_values[min(n - 1, 3 * n // 4)]), 1),
    )


# ============================================================
# 公开接口
# ============================================================


async def get_rotation_profile(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
) -> RotationProfileResponse:
    """
    获取指定专精在指定 Boss 上的循环数据聚合。

    从 Top 5 玩家采样，聚合施法次数、CPM、Buff 覆盖率和 DPS 分布。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "frost-death-knight"
        encounter_id: Boss 遭遇 ID
        difficulty: 难度 — "normal" / "heroic" / "mythic"

    Returns:
        RotationProfileResponse 包含循环统计数据
    """
    difficulty = difficulty or "heroic"
    cache_key = f"rotation:{spec}:{encounter_id}:{difficulty}"

    # ---- 缓存检查 ----
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_rotation_profile 缓存命中")
        return RotationProfileResponse(**cached)

    # ---- 解析参数 ----
    class_name, spec_name = _parse_spec(spec)
    diff_id = DIFFICULTY_MAP.get(difficulty, 4)

    # ---- Step 1: 获取排行榜 ----
    encounter_data = await _query_rankings(
        client, encounter_id, class_name, spec_name, diff_id
    )
    encounter_name = encounter_data.get("name", "")
    rankings_data = encounter_data.get("characterRankings", {})
    rankings = rankings_data.get("rankings", [])

    # 取 Top N 玩家
    top_rankings = rankings[:TOP_N_PLAYERS]
    logger.info(
        "get_rotation_profile: %s on %s (%s), 采样 %d 人",
        spec, encounter_name, difficulty, len(top_rankings),
    )

    if not top_rankings:
        return RotationProfileResponse(
            spec=spec,
            encounter_id=encounter_id,
            encounter_name=encounter_name,
            difficulty=difficulty,
        )

    # ---- Step 2-4: 收集每位玩家数据 ----
    players_data: list[dict] = []
    for ranking in top_rankings:
        result = await _collect_player_data(client, ranking)
        if result:
            players_data.append(result)

    if not players_data:
        return RotationProfileResponse(
            spec=spec,
            encounter_id=encounter_id,
            encounter_name=encounter_name,
            difficulty=difficulty,
        )

    # ---- Step 5: 聚合 ----
    top_spells = _aggregate_spell_stats(players_data)
    buff_uptimes = _aggregate_buff_uptimes(players_data)
    dps_median, dps_p25, dps_p75 = _aggregate_dps(players_data)

    # 战斗时长中位数
    fight_durations = [pd["fight_duration"] for pd in players_data]
    fight_duration_median = round(statistics.median(fight_durations), 1)

    # ---- 构建响应 ----
    response = RotationProfileResponse(
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=encounter_name,
        difficulty=difficulty,
        sample_size=len(players_data),
        fight_duration_median=fight_duration_median,
        top_spells=top_spells,
        buff_uptimes=buff_uptimes,
        dps_median=dps_median,
        dps_p25=dps_p25,
        dps_p75=dps_p75,
    )

    # ---- 写入缓存 ----
    cache_set(cache_key, response.model_dump())
    return response
