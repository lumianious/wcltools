"""
get_cooldown_timelines 工具 — 聚合顶尖玩家的技能冷却时间线。

从 WCL characterRankings 获取顶尖玩家报告，
批量查询施法事件，聚类分析施法时机共识。

WCL 数据流:
  1. characterRankings(includeCombatantInfo: false) → report.code + report.fightID
  2. report.masterData.actors → sourceID 匹配
  3. report.events(dataType: Casts) → 逐页获取施法事件

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
from typing import Optional

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.data import get_spec_spells, get_spell_name
from src.models import (
    AbilityTimeline,
    CastCluster,
    CoUsage,
    CooldownTimelineResponse,
)
from src.tools.builds import DIFFICULTY_MAP, SPEC_MAPPING
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时
MIN_COOLDOWN_SECONDS = 30  # 只追踪 CD >= 30s 的技能
CLUSTER_GAP_SECONDS = 15  # 聚类间隔阈值
HOLD_THRESHOLD_SECONDS = 5  # 延迟使用检测阈值
CO_USAGE_WINDOW_SECONDS = 3  # 共用技能检测窗口
MAX_UTILITY_CLUSTERS = 4  # utility 类技能最大聚类数


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
# 技能过滤
# ============================================================


def _build_tracked_spells(
    spec: str, abilities: Optional[list[int]] = None
) -> dict[int, dict]:
    """
    构建需要追踪的技能字典。

    返回 {spell_id: {name, cd_seconds, ability_type}}
    只保留 CD >= 30s 的技能。
    """
    spells = get_spec_spells(spec)
    tracked: dict[int, dict] = {}
    user_requested = set(abilities) if abilities else set()
    for s in spells:
        cd = s.get("cooldown", 0)
        if cd < MIN_COOLDOWN_SECONDS:
            continue
        sid = s.get("spell_id")
        if not sid:
            continue
        # 如果用户指定了技能列表，只保留指定的
        if user_requested and sid not in user_requested:
            continue
        # 未指定技能列表时，跳过无关技能:
        # 有 tags 的职业技能（dps/defensive/raid_cd/healing/tank）始终保留
        # 仅对无 tags 的技能（饰品/消耗品/外部增益）检查 show 标记
        if not user_requested and s.get("show") is False:
            tags = s.get("tags", [])
            has_meaningful_tag = bool(
                set(tags) & {"dps", "defensive", "raid_cd", "healing", "tank"}
            )
            if not has_meaningful_tag:
                continue
        tracked[sid] = {
            "name": s.get("name", f"Spell {sid}"),
            "cd_seconds": cd,
            "ability_type": _infer_ability_type(s),
        }
    return tracked


def _infer_ability_type(spell: dict) -> str:
    """从 tags 推断技能类型。"""
    tags = spell.get("tags", [])
    if "dps" in tags:
        return "offensive"
    if "healing" in tags or "defensive" in tags or "tank" in tags:
        return "defensive"
    if "buff" in tags or "raid_cd" in tags:
        return "buff"
    if "move" in tags or "dynamic_cd" in tags:
        return "utility"
    return "utility"


# ============================================================
# WCL 查询: 排行榜
# ============================================================


async def _query_rankings(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    difficulty: int,
    sample_size: int,
) -> tuple[str, list[dict]]:
    """
    查询 WCL 排行榜，返回 (encounter_name, rankings)。

    每条 ranking 包含: name, report.code, report.fightID
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
                    includeCombatantInfo: false
                    page: 1
                )
            }}
        }}
    """
    data = await client.query(gql)
    encounter = data.get("worldData", {}).get("encounter", {})
    enc_name = encounter.get("name", "")
    cr = encounter.get("characterRankings", {})
    rankings = cr.get("rankings", [])[:sample_size]
    logger.info(
        "排行榜: %s %s-%s, 获取 %d 条",
        enc_name, class_name, spec_name, len(rankings),
    )
    return enc_name, rankings


# ============================================================
# WCL 查询: masterData (按 report 分组)
# ============================================================


async def _query_master_data(
    client: WCLClient, report_code: str
) -> list[dict]:
    """查询报告的 masterData.actors，返回玩家列表。"""
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
                }}
            }}
        }}
    """
    data = await client.query(gql)
    report = data.get("reportData", {}).get("report", {})
    master = report.get("masterData", {})
    return master.get("actors", [])


def _find_actor_id(
    actors: list[dict], player_name: str
) -> Optional[int]:
    """在 actors 列表中按名字匹配玩家，返回 sourceID。"""
    for actor in actors:
        if actor.get("name") == player_name:
            return actor.get("id")
    return None


# ============================================================
# WCL 查询: 施法事件 (分页)
# ============================================================


async def _query_cast_events(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    source_id: int,
) -> list[dict]:
    """
    分页查询指定玩家在指定战斗中的所有施法事件。

    返回原始 event 列表 [{timestamp, type, abilityGameID, ...}]
    """
    all_events: list[dict] = []
    next_ts: Optional[int] = 0

    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(
                        startTime: {next_ts}
                        endTime: 99999999
                        fightIDs: [{fight_id}]
                        dataType: Casts
                        sourceID: {source_id}
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
# WCL 查询: 伤害事件 (分页)
# ============================================================


async def _query_damage_events(
    client: WCLClient,
    report_code: str,
    fight_id: int,
    source_id: int,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> list[dict]:
    """
    分页查询指定玩家在指定战斗中的所有伤害事件。

    返回原始 event 列表 [{timestamp, type, abilityGameID, amount, ...}]
    支持可选的 startTime/endTime 限制查询范围。
    """
    all_events: list[dict] = []
    next_ts: Optional[int] = start_time or 0
    end_val = end_time if end_time is not None else 99999999

    while next_ts is not None:
        gql = f"""
            reportData {{
                report(code: "{report_code}") {{
                    events(
                        startTime: {next_ts}
                        endTime: {end_val}
                        fightIDs: [{fight_id}]
                        dataType: DamageDone
                        sourceID: {source_id}
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
        all_events.extend(events_block.get("data", []))
        next_ts = events_block.get("nextPageTimestamp")

    return all_events


# ============================================================
# 批量获取施法数据（按 report 分组优化）
# ============================================================


async def _collect_cast_data(
    client: WCLClient,
    rankings: list[dict],
    tracked_spell_ids: set[int],
) -> tuple[list[dict], list[float]]:
    """
    批量收集所有排名玩家的施法数据。

    按 report code 分组，避免重复查询 masterData。
    返回:
      - casts: [{player, spell_id, relative_sec}]
      - durations: 每场战斗时长(秒)列表
    """
    # 按 report code 分组
    report_groups: dict[str, list[dict]] = defaultdict(list)
    for r in rankings:
        code = r.get("report", {}).get("code")
        if code:
            report_groups[code].append(r)

    all_casts: list[dict] = []
    fight_durations: list[float] = []

    for code, group in report_groups.items():
        # 每个 report code 只查一次 masterData
        try:
            actors = await _query_master_data(client, code)
        except Exception as exc:
            logger.warning("masterData 查询失败 %s: %s", code, exc)
            continue

        for r in group:
            casts, dur = await _fetch_player_casts(
                client, code, r, actors, tracked_spell_ids
            )
            all_casts.extend(casts)
            if dur > 0:
                fight_durations.append(dur)

    return all_casts, fight_durations


async def _fetch_player_casts(
    client: WCLClient,
    report_code: str,
    ranking: dict,
    actors: list[dict],
    tracked_spell_ids: set[int],
) -> tuple[list[dict], float]:
    """获取单个玩家的施法数据。查询失败时返回空列表和 0。"""
    player_name = ranking.get("name", "")
    fight_id = ranking.get("report", {}).get("fightID")
    duration = ranking.get("duration", 0)
    if not fight_id:
        return [], 0.0

    dur_sec = duration / 1000.0 if duration > 1000 else float(duration)

    actor_id = _find_actor_id(actors, player_name)
    if actor_id is None:
        logger.debug("未找到玩家 %s 在报告 %s 中", player_name, report_code)
        return [], 0.0

    try:
        events = await _query_cast_events(
            client, report_code, fight_id, actor_id
        )
    except Exception as exc:
        logger.warning("事件查询失败 %s/%s: %s", report_code, player_name, exc)
        return [], 0.0

    # 过滤追踪技能，转为相对时间
    fight_start = _get_fight_start(events)
    casts: list[dict] = [
        {
            "player": player_name,
            "spell_id": evt.get("abilityGameID"),
            "relative_sec": (evt.get("timestamp", 0) - fight_start) / 1000.0,
        }
        for evt in events
        if evt.get("abilityGameID") in tracked_spell_ids
    ]
    return casts, dur_sec


def _get_fight_start(events: list[dict]) -> int:
    """从事件列表中获取战斗开始时间（最早的时间戳）。"""
    if not events:
        return 0
    return min(e.get("timestamp", 0) for e in events)


# ============================================================
# 聚类分析
# ============================================================


def _cluster_timestamps(
    timestamps: list[float],
) -> list[list[float]]:
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


def _build_cluster(
    cluster_ts: list[float],
    total_players: int,
    unique_players_in_cluster: int,
) -> CastCluster:
    """从一组时间戳构建 CastCluster 对象。"""
    median = statistics.median(cluster_ts)
    std = (
        round(statistics.stdev(cluster_ts), 1)
        if len(cluster_ts) > 1
        else 0.0
    )
    pct = round(unique_players_in_cluster / total_players * 100, 1)
    return CastCluster(
        median_time=round(median, 1),
        std_dev=std,
        range=[round(min(cluster_ts), 1), round(max(cluster_ts), 1)],
        player_pct=pct,
    )


# ============================================================
# Hold 检测
# ============================================================


def _detect_holds(
    clusters: list[CastCluster], cd_seconds: float
) -> None:
    """
    检测延迟使用: 如果实际施法时间比 CD 转好晚 >5s。

    就地修改 clusters 的 hold 字段。
    """
    for i in range(1, len(clusters)):
        prev = clusters[i - 1]
        curr = clusters[i]
        off_cd_at = prev.median_time + cd_seconds
        held = curr.median_time - off_cd_at
        if held > HOLD_THRESHOLD_SECONDS:
            curr.hold = {
                "off_cd_at": round(off_cd_at, 1),
                "held_seconds": round(held, 1),
            }


# ============================================================
# 共用技能分析
# ============================================================


def _compute_co_usage(
    casts: list[dict],
    spell_id: int,
    cluster_ts_range: tuple[float, float],
    tracked_spells: dict[int, dict],
) -> list[CoUsage]:
    """
    计算某技能在某聚类时间段内与其他技能的共用率。

    对聚类内每次施法，检查同玩家 ±3s 内是否有其他追踪技能。
    """
    co_counts, total = _find_co_used_pairs(
        casts, spell_id, cluster_ts_range, tracked_spells
    )
    if total == 0:
        return []

    result: list[CoUsage] = []
    for sid, count in sorted(
        co_counts.items(), key=lambda x: x[1], reverse=True
    ):
        name = tracked_spells.get(sid, {}).get("name", "")
        if not name:
            name = get_spell_name(sid) or f"Spell {sid}"
        rate = round(count / total * 100, 1)
        if rate >= 10:  # 只报告 >= 10% 的共用
            result.append(CoUsage(ability=name, rate=rate))

    return result[:5]


def _find_co_used_pairs(
    casts: list[dict],
    spell_id: int,
    cluster_ts_range: tuple[float, float],
    tracked_spells: dict[int, dict],
) -> tuple[dict[int, int], int]:
    """
    检测聚类内与目标技能共用的其他技能。

    返回 (co_counts, total_in_cluster):
      - co_counts: {其他技能ID: 共用次数}
      - total_in_cluster: 聚类内使用该技能的玩家数
    """
    # 按玩家分组所有施法
    player_casts: dict[str, list[dict]] = defaultdict(list)
    for c in casts:
        player_casts[c["player"]].append(c)

    lo, hi = cluster_ts_range
    co_counts: dict[int, int] = defaultdict(int)
    total_in_cluster = 0

    for _player, pcasts in player_casts.items():
        # 找到该玩家在聚类时间范围内的目标技能施法
        target_casts = [
            c for c in pcasts
            if c["spell_id"] == spell_id
            and lo <= c["relative_sec"] <= hi
        ]
        if not target_casts:
            continue
        total_in_cluster += 1

        # 检查该玩家的其他技能在 ±3s 内的施法
        for tc in target_casts:
            t = tc["relative_sec"]
            for oc in pcasts:
                if oc["spell_id"] == spell_id:
                    continue
                if oc["spell_id"] not in tracked_spells:
                    continue
                if abs(oc["relative_sec"] - t) <= CO_USAGE_WINDOW_SECONDS:
                    co_counts[oc["spell_id"]] += 1
                    break  # 每个共用技能每次只计一次

    return co_counts, total_in_cluster


# ============================================================
# 共识文本生成
# ============================================================


def _generate_consensus(clusters: list[CastCluster]) -> str:
    """Generate consensus description based on clustering results."""
    if not clusters:
        return "insufficient data"
    high_agree = [c for c in clusters if c.player_pct >= 70]
    if len(high_agree) == len(clusters):
        return "high consensus"
    if any(c.player_pct >= 70 for c in clusters):
        return "partial consensus"
    return "low consensus"


# ============================================================
# 技能时间线聚合（单个技能）
# ============================================================


def _aggregate_ability(
    spell_id: int,
    spell_info: dict,
    all_casts: list[dict],
    tracked_spells: dict[int, dict],
    total_players: int,
) -> AbilityTimeline:
    """为单个技能聚合施法时间线。"""
    ability_casts = [c for c in all_casts if c["spell_id"] == spell_id]
    total_casts = _compute_ability_stats(ability_casts)

    # 聚类分析 + hold 检测
    cast_clusters = _cluster_ability_casts(
        ability_casts, all_casts, spell_id,
        spell_info, tracked_spells, total_players,
    )
    cd_sec = spell_info.get("cd_seconds", 0)
    _detect_holds(cast_clusters, cd_sec)

    # utility 类技能限制聚类数量
    if spell_info.get("ability_type") == "utility":
        cast_clusters.sort(key=lambda c: c.player_pct, reverse=True)
        cast_clusters = cast_clusters[:MAX_UTILITY_CLUSTERS]
        cast_clusters.sort(key=lambda c: c.median_time)

    # 添加标签
    for i, c in enumerate(cast_clusters):
        c.label = f"Cast {i + 1}"

    return AbilityTimeline(
        name=spell_info.get("name", f"Spell {spell_id}"),
        ability_type=spell_info.get("ability_type", ""),
        cd_seconds=cd_sec,
        total_casts=total_casts,
        cast_clusters=cast_clusters,
        consensus=_generate_consensus(cast_clusters),
    )


def _compute_ability_stats(ability_casts: list[dict]) -> dict[str, float]:
    """统计每位玩家的施法次数，返回 median/min/max。"""
    player_counts: dict[str, int] = defaultdict(int)
    for c in ability_casts:
        player_counts[c["player"]] += 1

    counts = list(player_counts.values())
    if not counts:
        return {}
    return {
        "median": round(statistics.median(counts), 1),
        "min": float(min(counts)),
        "max": float(max(counts)),
    }


def _cluster_ability_casts(
    ability_casts: list[dict],
    all_casts: list[dict],
    spell_id: int,
    spell_info: dict,
    tracked_spells: dict[int, dict],
    total_players: int,
) -> list[CastCluster]:
    """对单个技能的施法进行聚类，并计算共用技能。"""
    timestamps = [c["relative_sec"] for c in ability_casts]
    raw_clusters = _cluster_timestamps(timestamps)

    cast_clusters: list[CastCluster] = []
    for cluster_ts in raw_clusters:
        lo, hi = min(cluster_ts), max(cluster_ts)
        # 计算聚类内独立玩家数
        players_in = {
            c["player"] for c in ability_casts
            if lo <= c["relative_sec"] <= hi
        }
        cluster = _build_cluster(cluster_ts, total_players, len(players_in))
        cluster.co_used = _compute_co_usage(
            all_casts, spell_id, (lo, hi), tracked_spells
        )
        cast_clusters.append(cluster)

    return cast_clusters


# ============================================================
# 公开接口
# ============================================================


async def get_cooldown_timelines(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
    abilities: Optional[list[int]] = None,
    sample_size: int = 50,
) -> CooldownTimelineResponse:
    """
    获取指定专精在指定 Boss 上的技能冷却时间线聚合。

    Args:
        client: WCL API 客户端
        spec: 专精 slug，如 "frost-death-knight"
        encounter_id: Boss 遭遇 ID
        difficulty: 难度 — "normal" / "heroic" / "mythic"
        abilities: 可选，指定追踪的技能 ID 列表
        sample_size: 采样数量，默认 50

    Returns:
        CooldownTimelineResponse 包含所有技能时间线
    """
    difficulty = difficulty or "heroic"
    cache_key = (
        f"cooldown_timelines_{spec}_{encounter_id}"
        f"_{difficulty}_{sample_size}"
    )

    # ---- 缓存检查 ----
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("get_cooldown_timelines 缓存命中")
        return CooldownTimelineResponse(**cached)

    try:
        return await _fetch_and_aggregate(
            client, spec, encounter_id,
            difficulty, abilities, sample_size, cache_key,
        )
    except Exception as exc:
        logger.error("get_cooldown_timelines 失败: %s", exc)
        return CooldownTimelineResponse(
            spec=spec,
            encounter=f"Error: {exc}",
            difficulty=difficulty,
        )


async def _fetch_and_aggregate(
    client: WCLClient,
    spec: str,
    encounter_id: int,
    difficulty: str,
    abilities: Optional[list[int]],
    sample_size: int,
    cache_key: str,
) -> CooldownTimelineResponse:
    """执行实际的数据获取与聚合（从公开接口分离以控制函数长度）。"""
    class_name, spec_name = _parse_spec(spec)
    diff_id = DIFFICULTY_MAP.get(difficulty, 4)

    # 确定追踪技能
    tracked_spells = _build_tracked_spells(spec, abilities)
    if not tracked_spells:
        return _empty_response(spec, f"Encounter {encounter_id}", difficulty)

    tracked_ids = set(tracked_spells.keys())
    logger.info(
        "追踪 %d 个技能: %s",
        len(tracked_ids),
        ", ".join(s["name"] for s in tracked_spells.values()),
    )

    # 获取排行榜
    enc_name, rankings = await _query_rankings(
        client, encounter_id, class_name, spec_name, diff_id, sample_size,
    )
    if not rankings:
        return _empty_response(spec, enc_name or f"Encounter {encounter_id}", difficulty)

    # 批量获取施法数据
    all_casts, fight_durations = await _collect_cast_data(
        client, rankings, tracked_ids
    )
    logger.info(
        "共收集 %d 条施法记录, %d 场战斗",
        len(all_casts), len(fight_durations),
    )

    # 聚合 + 构建响应
    return _build_timeline_response(
        spec, enc_name or f"Encounter {encounter_id}", difficulty,
        rankings, tracked_spells, all_casts, fight_durations, cache_key,
    )


def _empty_response(
    spec: str, encounter: str, difficulty: str,
) -> CooldownTimelineResponse:
    """构建无数据的空响应。"""
    return CooldownTimelineResponse(
        spec=spec, encounter=encounter,
        difficulty=difficulty, sample_size=0,
    )


def _build_timeline_response(
    spec: str,
    encounter: str,
    difficulty: str,
    rankings: list[dict],
    tracked_spells: dict[int, dict],
    all_casts: list[dict],
    fight_durations: list[float],
    cache_key: str,
) -> CooldownTimelineResponse:
    """聚合所有技能时间线并构建最终响应。"""
    total_players = len(rankings)
    ability_timelines: list[AbilityTimeline] = []
    for sid, info in tracked_spells.items():
        timeline = _aggregate_ability(
            sid, info, all_casts, tracked_spells, total_players
        )
        if timeline.cast_clusters:
            ability_timelines.append(timeline)

    median_dur = (
        round(statistics.median(fight_durations), 1)
        if fight_durations else 0.0
    )
    response = CooldownTimelineResponse(
        spec=spec,
        encounter=encounter,
        difficulty=difficulty,
        sample_size=total_players,
        median_fight_duration=median_dur,
        abilities=ability_timelines,
    )
    cache_set(cache_key, response.model_dump())
    return response
