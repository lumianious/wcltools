"""
get_defensive_patterns 工具 — 分析顶尖玩家的防御技能使用模式。

从 WCL characterRankings 获取顶尖玩家报告，
查询死亡事件、防御技能施法事件和受到伤害数据，
聚类分析防御技能使用时机和死亡分布。

WCL 数据流:
  1. characterRankings(includeCombatantInfo: true) → report.code + report.fightID
  2. report.fights → startTime / endTime / kill
  3. report.events(Deaths) → 死亡时机和致死技能
  4. report.events(Casts, abilityID) → 防御技能施法时机
  5. report.table(DamageTaken) → 受到伤害统计

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
# 第三方库
# ============================================================
from pydantic import BaseModel, Field

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.data import get_spec_spells, get_spell_name
from src.tools.builds import DIFFICULTY_MAP, SPEC_MAPPING
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时
CLUSTER_GAP_SECONDS = 15  # 聚类间隔阈值
SAMPLE_SIZE = 10  # 采样 top 10 玩家


# ============================================================
# 响应模型（定义在本文件内，避免 models.py 冲突）
# ============================================================


class DefensiveTiming(BaseModel):
    """防御技能的典型使用时机。"""
    name: str
    spell_id: int
    clusters: list[dict] = Field(default_factory=list)
    usage_rate: float = 0.0


class DeathWindow(BaseModel):
    """死亡集中出现的时间窗口。"""
    time_range: str = ""
    median_time: float = 0.0
    death_count: int = 0
    common_causes: list[str] = Field(default_factory=list)


class DefensivePatternResponse(BaseModel):
    """某专精在某 Boss 上的防御模式分析。"""
    spec: str = ""
    encounter_id: int = 0
    encounter_name: str = ""
    difficulty: str = "heroic"
    sample_size: int = 0
    fight_duration_median: float = 0.0
    defensive_timings: list[DefensiveTiming] = Field(default_factory=list)
    death_windows: list[DeathWindow] = Field(default_factory=list)
    survival_rate: float = 0.0


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
# 防御技能列表构建
# ============================================================


def _get_defensive_spells(spec: str) -> dict[int, str]:
    """
    从 specs.json 获取带 "defensive" 标签的技能。

    返回 {spell_id: name}
    """
    spells = get_spec_spells(spec)
    defensives: dict[int, str] = {}
    for s in spells:
        tags = s.get("tags", [])
        if "defensive" in tags:
            sid = s.get("spell_id")
            if sid:
                defensives[sid] = s.get("name", f"Spell {sid}")
    return defensives


# ============================================================
# 聚类分析（与 timelines.py 相同的间隔聚类逻辑）
# ============================================================


def _cluster_timestamps(timestamps: list[float]) -> list[list[float]]:
    """
    密度聚类: 按时间排序，间隔 > 15s 则开新簇。

    返回 [[ts1, ts2, ...], [ts3, ts4, ...], ...]
    """
    if not timestamps:
        return []
    sorted_ts = sorted(timestamps)
    clusters: list[list[float]] = [[sorted_ts[0]]]
    for ts in sorted_ts[1:]:
        cluster_mean = statistics.mean(clusters[-1])
        if ts - cluster_mean > CLUSTER_GAP_SECONDS:
            clusters.append([ts])
        else:
            clusters[-1].append(ts)
    return clusters


# ============================================================
# WCL 查询: 排行榜
# ============================================================


async def _query_rankings(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    difficulty: int,
) -> tuple[str, list[dict]]:
    """
    查询 WCL 排行榜，返回 (encounter_name, rankings)。

    每条 ranking 包含: name, report.code, report.fightID, duration
    """
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
    encounter = data.get("worldData", {}).get("encounter", {})
    enc_name = encounter.get("name", "")
    cr = encounter.get("characterRankings", {})
    rankings = cr.get("rankings", [])[:SAMPLE_SIZE]
    logger.info(
        "防御分析排行榜: %s %s-%s, 获取 %d 条",
        enc_name, class_name, spec_name, len(rankings),
    )
    return enc_name, rankings


# ============================================================
# WCL 查询: 战斗信息 + 死亡事件
# ============================================================


async def _query_fight_and_deaths(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> tuple[dict, list[dict]]:
    """
    查询单场战斗的基本信息和死亡事件。

    返回 (fight_info, death_events)
    fight_info = {startTime, endTime, kill}
    death_events = [{timestamp, targetID, killingAbility: {name, guid}}]
    """
    # 先获取战斗时间范围
    fight_gql = f"""
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
    fight_data = await client.query(fight_gql)
    report = fight_data.get("reportData", {}).get("report", {})
    fights = report.get("fights", [])
    if not fights:
        return {}, []

    fight_info = fights[0]
    start = fight_info.get("startTime", 0)
    end = fight_info.get("endTime", 0)
    if not start or not end:
        return fight_info, []

    # 查询死亡事件
    death_gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                events(
                    startTime: {start}
                    endTime: {end}
                    dataType: Deaths
                    limit: 500
                ) {{
                    data
                }}
            }}
        }}
    """
    death_data = await client.query(death_gql)
    death_report = death_data.get("reportData", {}).get("report", {})
    death_events = death_report.get("events", {}).get("data", [])

    return fight_info, death_events


# ============================================================
# WCL 查询: 防御技能施法事件
# ============================================================


async def _query_defensive_casts(
    client: WCLClient,
    report_code: str,
    start: int,
    end: int,
    spell_id: int,
) -> list[dict]:
    """
    查询指定防御技能的施法事件。

    返回 [{timestamp, sourceID, abilityGameID, ...}]
    """
    gql = f"""
        reportData {{
            report(code: "{report_code}") {{
                events(
                    startTime: {start}
                    endTime: {end}
                    dataType: Casts
                    abilityID: {spell_id}
                    limit: 10000
                ) {{
                    data
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    return report.get("events", {}).get("data", [])


# ============================================================
# 数据收集: 批量获取所有报告的防御数据
# ============================================================


async def _collect_defensive_data(
    client: WCLClient,
    rankings: list[dict],
    defensive_spells: dict[int, str],
) -> tuple[
    list[float],   # fight_durations
    list[dict],    # death_records: [{relative_sec, killing_ability}]
    dict[int, list[float]],  # spell_casts: {spell_id: [relative_sec, ...]}
    int,           # survived_count
    int,           # total_count
]:
    """
    批量收集所有排名玩家的防御相关数据。

    按 report code 分组查询，避免重复请求。
    """
    # 按 (report_code, fight_id) 去重，避免同一场战斗查询多次
    seen_fights: dict[tuple[str, int], dict] = {}
    for r in rankings:
        report = r.get("report", {})
        code = report.get("code")
        fid = report.get("fightID")
        if code and fid:
            key = (code, fid)
            if key not in seen_fights:
                seen_fights[key] = {
                    "code": code,
                    "fight_id": fid,
                    "duration": r.get("duration", 0),
                }

    fight_durations: list[float] = []
    all_deaths: list[dict] = []
    spell_casts: dict[int, list[float]] = defaultdict(list)
    survived_count = 0
    total_count = 0

    for (code, fid), info in seen_fights.items():
        # 记录战斗时长
        dur = info["duration"]
        dur_sec = dur / 1000.0 if dur > 1000 else dur
        if dur_sec > 0:
            fight_durations.append(dur_sec)

        # 获取战斗信息和死亡事件
        try:
            fight_info, death_events = await _query_fight_and_deaths(
                client, code, fid
            )
        except Exception as exc:
            logger.warning("战斗/死亡查询失败 %s/%d: %s", code, fid, exc)
            continue

        start = fight_info.get("startTime", 0)
        end = fight_info.get("endTime", 0)
        is_kill = fight_info.get("kill", False)

        if not start or not end:
            continue

        total_count += 1
        # 如果是击杀且无死亡事件，记为存活
        if is_kill and not death_events:
            survived_count += 1
        elif is_kill:
            # 击杀战斗中仍可能有死亡（其他人死了）
            # 简化处理：击杀 = 存活
            survived_count += 1

        # 处理死亡事件
        for evt in death_events:
            ts = evt.get("timestamp", 0)
            relative_sec = (ts - start) / 1000.0
            # WCL 死亡事件使用 killingAbilityGameID (int)，需解析名称
            killing_id = evt.get("killingAbilityGameID", 0)
            ability_name = (
                get_spell_name(killing_id) if killing_id else "Unknown"
            ) or "Unknown"
            all_deaths.append({
                "relative_sec": relative_sec,
                "killing_ability": ability_name,
            })

        # 查询每个防御技能的施法事件
        for spell_id in defensive_spells:
            try:
                casts = await _query_defensive_casts(
                    client, code, start, end, spell_id
                )
            except Exception as exc:
                logger.warning(
                    "防御技能查询失败 %s/%d spell %d: %s",
                    code, fid, spell_id, exc,
                )
                continue

            for cast in casts:
                ts = cast.get("timestamp", 0)
                relative_sec = (ts - start) / 1000.0
                spell_casts[spell_id].append(relative_sec)

    return fight_durations, all_deaths, spell_casts, survived_count, total_count


# ============================================================
# 聚合分析: 防御技能时机
# ============================================================


def _aggregate_defensive_timings(
    defensive_spells: dict[int, str],
    spell_casts: dict[int, list[float]],
    total_fights: int,
) -> list[DefensiveTiming]:
    """
    聚合每个防御技能的施法时机。

    对每个技能的施法时间戳进行聚类，计算使用率。
    """
    timings: list[DefensiveTiming] = []

    for spell_id, name in defensive_spells.items():
        casts = spell_casts.get(spell_id, [])
        if not casts:
            timings.append(DefensiveTiming(
                name=name,
                spell_id=spell_id,
                clusters=[],
                usage_rate=0.0,
            ))
            continue

        # 聚类施法时间
        raw_clusters = _cluster_timestamps(casts)

        # 构建聚类信息
        cluster_info: list[dict] = []
        for cluster_ts in raw_clusters:
            median_t = round(statistics.median(cluster_ts), 1)
            # player_pct: 该聚类中的施法次数 / 总战斗数
            pct = round(len(cluster_ts) / total_fights * 100, 1) if total_fights > 0 else 0.0
            cluster_info.append({
                "median_time": median_t,
                "player_pct": min(pct, 100.0),
            })

        # 使用率: 有施法记录的战斗占比
        usage_rate = round(len(casts) / max(total_fights, 1) * 100, 1)

        timings.append(DefensiveTiming(
            name=name,
            spell_id=spell_id,
            clusters=cluster_info,
            usage_rate=min(usage_rate, 100.0),
        ))

    return timings


# ============================================================
# 聚合分析: 死亡时间窗口
# ============================================================


def _aggregate_death_windows(
    deaths: list[dict],
) -> list[DeathWindow]:
    """
    聚类分析死亡时机分布。

    将死亡时间戳聚类，统计每个窗口的死亡数和常见致死技能。
    """
    if not deaths:
        return []

    timestamps = [d["relative_sec"] for d in deaths]
    raw_clusters = _cluster_timestamps(timestamps)

    windows: list[DeathWindow] = []
    for cluster_ts in raw_clusters:
        median_t = round(statistics.median(cluster_ts), 1)
        lo = round(min(cluster_ts), 0)
        hi = round(max(cluster_ts), 0)
        time_range = f"{int(lo)}s-{int(hi)}s"

        # 统计该窗口内的致死技能
        causes: dict[str, int] = defaultdict(int)
        for d in deaths:
            if lo <= d["relative_sec"] <= hi:
                ability = d["killing_ability"]
                if ability and ability != "Unknown":
                    causes[ability] += 1

        # 按频次排序取 top 3
        top_causes = [
            name for name, _ in sorted(
                causes.items(), key=lambda x: x[1], reverse=True
            )
        ][:3]

        windows.append(DeathWindow(
            time_range=time_range,
            median_time=median_t,
            death_count=len(cluster_ts),
            common_causes=top_causes,
        ))

    # 按死亡数降序排序
    windows.sort(key=lambda w: w.death_count, reverse=True)
    return windows


# ============================================================
# 公开接口
# ============================================================


async def get_defensive_patterns(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
) -> DefensivePatternResponse:
    """
    获取指定专精在指定 Boss 上的防御模式分析。

    分析顶尖玩家的防御技能使用时机、死亡分布和存活率。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "frost-death-knight"
        encounter_id: Boss 遭遇 ID
        difficulty: 难度 — "normal" / "heroic" / "mythic"

    Returns:
        DefensivePatternResponse 包含防御时机、死亡窗口和存活率
    """
    difficulty = difficulty or "heroic"
    cache_key = f"defensives:{spec}:{encounter_id}:{difficulty}"

    # ---- 缓存检查 ----
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_defensive_patterns 缓存命中")
        return DefensivePatternResponse(**cached)

    try:
        return await _fetch_and_analyze(
            client, spec, encounter_id, difficulty, cache_key,
        )
    except Exception as exc:
        logger.error("get_defensive_patterns 失败: %s", exc)
        return DefensivePatternResponse(
            spec=spec,
            encounter_id=encounter_id,
            encounter_name=f"Error: {exc}",
            difficulty=difficulty,
        )


async def _fetch_and_analyze(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str,
    cache_key: str,
) -> DefensivePatternResponse:
    """执行实际的数据获取与分析（从公开接口分离以控制函数长度）。"""

    # ---- Step 1: 解析参数 ----
    class_name, spec_name = _parse_spec(spec)
    diff_id = DIFFICULTY_MAP.get(difficulty, 4)

    # ---- Step 2: 获取防御技能列表 ----
    defensive_spells = _get_defensive_spells(spec)
    if not defensive_spells:
        logger.warning("未找到 %s 的防御技能", spec)
        return DefensivePatternResponse(
            spec=spec,
            encounter_id=encounter_id,
            difficulty=difficulty,
        )

    logger.info(
        "追踪 %d 个防御技能: %s",
        len(defensive_spells),
        ", ".join(defensive_spells.values()),
    )

    # ---- Step 3: 获取排行榜 ----
    enc_name, rankings = await _query_rankings(
        client, encounter_id, class_name, spec_name, diff_id,
    )
    if not rankings:
        return DefensivePatternResponse(
            spec=spec,
            encounter_id=encounter_id,
            encounter_name=enc_name or f"Encounter {encounter_id}",
            difficulty=difficulty,
            sample_size=0,
        )

    # ---- Step 4: 批量收集防御数据 ----
    (
        fight_durations,
        all_deaths,
        spell_casts,
        survived_count,
        total_count,
    ) = await _collect_defensive_data(client, rankings, defensive_spells)

    logger.info(
        "防御数据收集完成: %d 场战斗, %d 次死亡, %d 次防御技能施法",
        total_count,
        len(all_deaths),
        sum(len(v) for v in spell_casts.values()),
    )

    # ---- Step 5: 聚合分析 ----
    defensive_timings = _aggregate_defensive_timings(
        defensive_spells, spell_casts, total_count,
    )
    death_windows = _aggregate_death_windows(all_deaths)

    # ---- Step 6: 计算存活率 ----
    survival_rate = (
        round(survived_count / total_count * 100, 1)
        if total_count > 0 else 0.0
    )

    # ---- Step 7: 战斗时长中位数 ----
    median_dur = (
        round(statistics.median(fight_durations), 1)
        if fight_durations else 0.0
    )

    # ---- Step 8: 构建响应 ----
    response = DefensivePatternResponse(
        spec=spec,
        encounter_id=encounter_id,
        encounter_name=enc_name or f"Encounter {encounter_id}",
        difficulty=difficulty,
        sample_size=len(rankings),
        fight_duration_median=median_dur,
        defensive_timings=defensive_timings,
        death_windows=death_windows,
        survival_rate=survival_rate,
    )

    # ---- 写入缓存 ----
    cache_set(cache_key, response.model_dump())
    return response
