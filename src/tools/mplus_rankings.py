"""
M+ 排行榜查询 — 从 WCL characterRankings 获取 M+ 顶尖玩家数据。

查询模式: characterRankings(difficulty: 10, bracket: N)
缓存策略: mplus_bench:{spec}:{encounter_id}:k{key_level}，TTL 6 小时
稀疏 bracket 回退: 结果 < 3 时尝试相邻 bracket (per D-02)

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
import statistics
from typing import Any

# ============================================================
# 本地模块
# ============================================================
from src.cache import cache_get, cache_set
from src.models import MplusBenchmarkMeta, MplusRankingEntry
from src.tools.builds import SPEC_MAPPING
from src.wcl_client import WCLClient

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_TTL_SECONDS = 6 * 3600  # 6 小时
MPLUS_DIFFICULTY = 10  # M+ difficulty ID（已通过 live API 验证）
DEFAULT_SAMPLE_SIZE = 5  # 每次查询返回的最大条目数 (per D-04)
MIN_RANKINGS_THRESHOLD = 3  # 低于此值触发相邻 bracket 回退 (per D-02)


# ============================================================
# Spec 解析（复用 builds.py 的 SPEC_MAPPING）
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
# WCL 查询
# ============================================================


async def _query_rankings_raw(
    client: WCLClient,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    bracket: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    查询 WCL M+ 排行榜原始数据。

    bracket 为最低钥石等级过滤（WCL bracket 是最小值过滤，非精确匹配）。
    返回 (encounter_name, rankings_list)。
    """
    # 构建可选的 bracket 参数
    bracket_param = f"\n                    bracket: {bracket}" if bracket is not None else ""

    gql = f"""
        worldData {{
            encounter(id: {encounter_id}) {{
                name
                characterRankings(
                    className: "{class_name}"
                    specName: "{spec_name}"
                    metric: dps
                    difficulty: {MPLUS_DIFFICULTY}{bracket_param}
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
    rankings = cr.get("rankings", [])
    return enc_name, rankings


# ============================================================
# 排行榜解析
# ============================================================


def _parse_rankings(
    rankings: list[dict[str, Any]], sample_size: int
) -> list[MplusRankingEntry]:
    """
    将 WCL 排行榜原始数据解析为 MplusRankingEntry 列表。

    从嵌套的 report.code / report.fightID 提取报告信息。
    """
    entries: list[MplusRankingEntry] = []
    for r in rankings[:sample_size]:
        report = r.get("report", {})
        entry = MplusRankingEntry(
            name=r.get("name", ""),
            class_name=r.get("class", ""),
            spec=r.get("spec", ""),
            amount=r.get("amount", 0.0),
            duration=r.get("duration", 0),
            bracket_data=r.get("bracketData", 0),
            report_code=report.get("code", ""),
            fight_id=report.get("fightID", 0),
        )
        entries.append(entry)
    return entries


def _compute_dps_stats(
    entries: list[MplusRankingEntry],
) -> tuple[float, float, float]:
    """
    计算 DPS 统计: (median, p25, p75)。

    使用 statistics.median 和 statistics.quantiles。
    """
    if not entries:
        return 0.0, 0.0, 0.0
    amounts = [e.amount for e in entries]
    median = statistics.median(amounts)
    if len(amounts) >= 2:
        qs = statistics.quantiles(amounts, n=4)
        p25, p75 = qs[0], qs[2]
    else:
        p25 = p75 = median
    return round(median, 1), round(p25, 1), round(p75, 1)


# ============================================================
# 公开接口
# ============================================================


async def query_mplus_rankings(
    client: WCLClient,
    encounter_id: int,
    spec: str,
    key_level: int | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> tuple[MplusBenchmarkMeta, list[MplusRankingEntry]]:
    """
    查询 M+ 排行榜数据。

    Args:
        client: WCL API 客户端
        encounter_id: 地下城遭遇 ID
        spec: 专精 slug，如 "frost-mage"
        key_level: 钥石等级（None 时不过滤 bracket）
        sample_size: 最大返回条目数（默认 5）

    Returns:
        (MplusBenchmarkMeta, list[MplusRankingEntry])
    """
    # 缓存键
    bracket_key = f"k{key_level}" if key_level is not None else "kall"
    cache_key = f"mplus_bench:{spec}:{encounter_id}:{bracket_key}"

    # 尝试缓存
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached is not None:
        logger.info("M+ rankings 缓存命中: %s", cache_key)
        meta = MplusBenchmarkMeta(**cached["meta"])
        entries = [MplusRankingEntry(**e) for e in cached["entries"]]
        return meta, entries

    # 解析 spec
    class_name, spec_name = _parse_spec(spec)

    # 查询 WCL
    enc_name, rankings = await _query_rankings_raw(
        client, encounter_id, class_name, spec_name,
        bracket=key_level,
    )

    actual_bracket = key_level or 0

    # 稀疏 bracket 回退 (per D-02)
    if key_level is not None and len(rankings) < MIN_RANKINGS_THRESHOLD:
        logger.info(
            "M+ bracket %d 稀疏 (%d 条)，尝试相邻 bracket",
            key_level, len(rankings),
        )
        # 尝试 key_level + 1
        _, fallback_rankings = await _query_rankings_raw(
            client, encounter_id, class_name, spec_name,
            bracket=key_level + 1,
        )
        if len(fallback_rankings) >= MIN_RANKINGS_THRESHOLD:
            rankings = fallback_rankings
            actual_bracket = key_level + 1
        else:
            # 尝试 key_level - 1（最低 bracket = 2）
            if key_level - 1 >= 2:
                _, fallback_rankings = await _query_rankings_raw(
                    client, encounter_id, class_name, spec_name,
                    bracket=key_level - 1,
                )
                if len(fallback_rankings) >= MIN_RANKINGS_THRESHOLD:
                    rankings = fallback_rankings
                    actual_bracket = key_level - 1

    # 解析排行榜
    entries = _parse_rankings(rankings, sample_size)

    # 计算 DPS 统计
    median_dps, dps_p25, dps_p75 = _compute_dps_stats(entries)

    meta = MplusBenchmarkMeta(
        encounter_id=encounter_id,
        encounter_name=enc_name,
        spec=spec,
        key_level=key_level or 0,
        actual_bracket=actual_bracket,
        sample_size=len(entries),
        median_dps=median_dps,
        dps_p25=dps_p25,
        dps_p75=dps_p75,
    )

    logger.info(
        "M+ rankings: %s on %s, key=%s, actual=%d, %d entries",
        spec, enc_name, key_level, actual_bracket, len(entries),
    )

    # 写入缓存
    cache_data = {
        "meta": meta.model_dump(),
        "entries": [e.model_dump() for e in entries],
    }
    cache_set(cache_key, cache_data)

    return meta, entries
