"""
WoW 团本 / 大秘境教练 MCP 服务器 — 入口模块。

传输协议: stdio（stdout 专用于 JSON-RPC，日志全部走 stderr）
工具注册: get_encounters, get_top_builds, get_spec_info, get_cooldown_timelines, get_rotation_profile, get_defensive_patterns, get_example_logs, analyze_player_log, analyze_dungeon_run, get_mplus_benchmarks, get_cast_sequence, get_buff_timeline, get_resource_timeline, get_boss_cast_timeline, get_coaching_context

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

# ============================================================
# 标准库
# ============================================================
import logging
import os
import sys
from typing import Optional

# ============================================================
# 第三方库
# ============================================================
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ============================================================
# 本地模块
# ============================================================
from src.tools.encounters import get_encounters as _get_encounters
from src.tools.builds import get_top_builds as _get_top_builds
from src.tools.spec_info import get_spec_info as _get_spec_info
from src.tools.timelines import get_cooldown_timelines as _get_cooldown_timelines
from src.tools.rotation import get_rotation_profile as _get_rotation_profile
from src.tools.defensives import get_defensive_patterns as _get_defensive_patterns
from src.tools.examples import get_example_logs as _get_example_logs
from src.tools.analyze import analyze_player_log as _analyze_player_log
from src.tools.coaching import get_coaching_context as _get_coaching_context
from src.tools.cast_sequence import get_cast_sequence as _get_cast_sequence
from src.tools.buff_timeline import get_buff_timeline as _get_buff_timeline
from src.tools.resource_timeline import get_resource_timeline as _get_resource_timeline
from src.tools.boss_timeline import get_boss_cast_timeline as _get_boss_cast_timeline
from src.tools.dungeon_analysis import analyze_dungeon_run as _analyze_dungeon_run
from src.tools.mplus_benchmarks import get_mplus_benchmarks as _get_mplus_benchmarks
from src.wcl_client import WCLClient

# ============================================================
# 日志配置 — 强制输出到 stderr
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ============================================================
# 加载环境变量
# ============================================================
# 尝试从多个路径加载 .env
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=_env_path)
load_dotenv()  # fallback: 当前工作目录

# ============================================================
# MCP 服务器实例
# ============================================================
mcp = FastMCP("wow-coach")

# ============================================================
# 全局 WCL 客户端（延迟初始化）
# ============================================================
_wcl_client: Optional[WCLClient] = None


def _get_wcl_client() -> WCLClient:
    """获取或创建 WCL 客户端单例。"""
    global _wcl_client
    if _wcl_client is None:
        client_id = os.environ.get("WCL_CLIENT_ID", "")
        client_secret = os.environ.get("WCL_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError(
                "缺少 WCL_CLIENT_ID 或 WCL_CLIENT_SECRET 环境变量。"
                "请在 .env 文件中配置。"
            )
        _wcl_client = WCLClient(client_id, client_secret)
        logger.info("WCL 客户端已初始化")
    return _wcl_client


# ============================================================
# 工具: get_encounters
# ============================================================


@mcp.tool()
async def get_encounters(
    content_type: str = "all",
) -> dict:
    """
    Discover current raid and dungeon encounters.

    Returns a list of zones (raids/dungeons) and their boss encounters
    with IDs, for the current WoW expansion.
    Call get_coaching_context first for session setup. Cost: ~1 WCL point.

    Args:
        content_type: Filter type - "raid", "mythic_plus", or "all"
    """
    client = _get_wcl_client()
    # 验证参数
    valid_types = ("raid", "mythic_plus", "all")
    if content_type not in valid_types:
        content_type = "all"

    result = await _get_encounters(client, content_type=content_type)  # type: ignore
    return result.model_dump()


# ============================================================
# 工具: get_top_builds
# ============================================================


@mcp.tool()
async def get_top_builds(
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
) -> dict:
    """
    Get aggregated talent builds, trinkets, and stat profiles
    from top-ranking players for a specific spec on a boss.

    Returns the top 2-3 talent builds with usage percentages,
    import strings, flex nodes, top trinkets, and stat distributions.
    Cost: ~2 WCL points. Results cached 6 hours.

    Args:
        spec: Class spec slug, e.g. "frost-death-knight", "holy-paladin"
        encounter_id: WCL encounter ID (use get_encounters to discover)
        difficulty: "normal", "heroic", or "mythic"
    """
    client = _get_wcl_client()
    result = await _get_top_builds(
        client, spec=spec, encounter_id=encounter_id, difficulty=difficulty
    )
    return result.model_dump()


# ============================================================
# 工具: get_spec_info
# ============================================================


@mcp.tool()
async def get_spec_info(
    spec: str = "",
    include_spells: bool = True,
) -> dict:
    """
    Look up WoW class/spec/spell static data.

    When spec is given: returns full spec data with spells categorized
    by type (offensive, defensive, utility, buff).
    When spec is omitted: returns a compact summary of all specs.

    No WCL API call needed — serves local data only. Cost: 0 points.

    Args:
        spec: Spec slug e.g. "frost-death-knight". Omit for all specs.
        include_spells: Whether to include spell details (default true)
    """
    return await _get_spec_info(
        spec=spec or None,
        include_spells=include_spells,
    )


# ============================================================
# 工具: get_cooldown_timelines
# ============================================================


@mcp.tool()
async def get_cooldown_timelines(
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
    abilities: Optional[list[int]] = None,
    sample_size: int = 50,
) -> dict:
    """
    Get aggregated cooldown usage timelines from top-ranking players.

    Analyzes when top players use major cooldowns during a boss fight,
    showing cast timing clusters, hold patterns, and co-usage rates.
    sample_size default 50 costs ~150 points. For coaching use 20-25
    to conserve rate limit (3600 pts/hr budget). Results cached 6 hours.

    Args:
        spec: Class spec slug, e.g. "frost-death-knight", "holy-paladin"
        encounter_id: WCL encounter ID (use get_encounters to discover)
        difficulty: "normal", "heroic", or "mythic"
        abilities: Optional list of spell IDs to track (default: all major CDs)
        sample_size: Number of top players to analyze (default 50)
    """
    client = _get_wcl_client()
    result = await _get_cooldown_timelines(
        client,
        spec=spec,
        encounter_id=encounter_id,
        difficulty=difficulty,
        abilities=abilities,
        sample_size=sample_size,
    )
    return result.model_dump()


# ============================================================
# 工具: get_rotation_profile
# ============================================================


@mcp.tool()
async def get_rotation_profile(
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
) -> dict:
    """
    Get aggregated rotation data from top-ranking players for a spec on a boss.

    Analyzes cast counts, casts-per-minute (CPM), buff uptimes, and DPS
    distribution from top 5 players. Use this to diagnose rotation problems
    (e.g. "your Obliterate count is p5 of top players").
    Samples top 5 players — treat as directional, not definitive.
    Cost: ~80 points first call, cached 6 hours after.

    Args:
        spec: Class spec slug, e.g. "frost-death-knight", "holy-paladin"
        encounter_id: WCL encounter ID (use get_encounters to discover)
        difficulty: "normal", "heroic", or "mythic"
    """
    client = _get_wcl_client()
    result = await _get_rotation_profile(
        client,
        spec=spec,
        encounter_id=encounter_id,
        difficulty=difficulty,
    )
    return result.model_dump()


# ============================================================
# 工具: get_defensive_patterns
# ============================================================


@mcp.tool()
async def get_defensive_patterns(
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
) -> dict:
    """
    Analyze defensive cooldown usage patterns, death timing, and survival
    rates from top-ranking players for a spec on a boss.

    Shows when top players use defensives, when deaths cluster in the fight,
    and what abilities kill people. Use this to answer "I keep dying in P2"
    questions. Samples top 10 players. Cost: ~60 points first call, cached 6 hours.

    Args:
        spec: Class spec slug, e.g. "frost-death-knight", "holy-paladin"
        encounter_id: WCL encounter ID (use get_encounters to discover)
        difficulty: "normal", "heroic", or "mythic"
    """
    client = _get_wcl_client()
    result = await _get_defensive_patterns(
        client,
        spec=spec,
        encounter_id=encounter_id,
        difficulty=difficulty,
    )
    return result.model_dump()


# ============================================================
# 工具: get_example_logs
# ============================================================


@mcp.tool()
async def get_example_logs(
    spec: str,
    encounter_id: int,
    difficulty: str = "heroic",
    count: int = 5,
) -> dict:
    """
    Get 3-5 exemplary WCL report URLs for a given spec/boss/difficulty.

    Returns direct links to top players' logs so users can study
    their gameplay, cooldown usage, and positioning.
    Filters out anonymous players. Cost: ~1 WCL point. Cached 6 hours.

    Args:
        spec: Class spec slug, e.g. "frost-death-knight", "holy-paladin"
        encounter_id: WCL encounter ID (use get_encounters to discover)
        difficulty: "normal", "heroic", or "mythic"
        count: Number of logs to return (3-5)
    """
    client = _get_wcl_client()
    result = await _get_example_logs(
        client,
        spec=spec,
        encounter_id=encounter_id,
        difficulty=difficulty,
        count=count,
    )
    return result.model_dump()


# ============================================================
# 工具: analyze_player_log
# ============================================================


@mcp.tool()
async def analyze_player_log(
    report: str,
    fight_id: int,
    player: str,
    spec: str,
    difficulty: str = "heroic",
) -> dict:
    """
    Analyze a player's performance in a specific WCL fight.

    Collects the player's casts, buffs, talents, and deaths from the log,
    then compares against benchmark data from top players to produce
    a structured gap analysis with actionable improvement suggestions.

    Accepts a report code (e.g. "ABC123") or a full WCL URL
    (e.g. "https://www.warcraftlogs.com/reports/ABC123#fight=3").
    Auto-detects encounter from the report. Fetches benchmarks in parallel
    (cached). Cost: ~5-7 points for player data + benchmark cost if not cached.

    Args:
        report: WCL report code or full URL
        fight_id: Fight ID within the report
        player: Character name (case-insensitive)
        spec: Class spec slug, e.g. "balance-druid", "frost-death-knight"
        difficulty: "normal", "heroic", or "mythic"
    """
    client = _get_wcl_client()
    result = await _analyze_player_log(
        client,
        report=report,
        fight_id=fight_id,
        player=player,
        spec=spec,
        difficulty=difficulty,
    )
    return result.model_dump()


# ============================================================
# 工具: analyze_dungeon_run
# ============================================================


@mcp.tool()
async def analyze_dungeon_run(
    report: str,
    player: str,
    spec: str,
    fight: str = "last",
    include_casts: bool = False,
) -> dict:
    """
    Analyze a player's performance across an entire M+ dungeon run.

    Aggregates damage, deaths, buff uptimes, and optionally cast data
    across all fight segments (bosses + trash) in a single dungeon run.
    Returns overall DPS (using active fight time, not wall-clock),
    per-segment breakdown, and top improvement areas.

    A WCL report may contain multiple dungeon runs. Use the fight parameter
    to select which run to analyze:
    - "last" (default): the most recent dungeon run
    - "1", "2", etc: the Nth run in chronological order
    - dungeon name (fuzzy): e.g. "Magisters" or "Pit of Saron"

    Accepts a report code or full WCL URL. Default cost: ~5-7 WCL points.
    With include_casts=True: +30-100 points (full cast pagination).

    Args:
        report: WCL report code or full URL
        player: Character name (case-insensitive)
        spec: Class spec slug, e.g. "balance-druid", "frost-death-knight"
        fight: Which dungeon run to analyze — "last", index, or name match
        include_casts: Enable full cast analysis (expensive, default false)
    """
    client = _get_wcl_client()
    result = await _analyze_dungeon_run(
        client,
        report=report,
        player=player,
        spec=spec,
        fight=fight,
        include_casts=include_casts,
    )
    return result.model_dump()


# ============================================================
# 工具: get_mplus_benchmarks
# ============================================================


@mcp.tool()
async def get_mplus_benchmarks(
    spec: str,
    encounter_id: int,
    key_level: int,
) -> dict:
    """
    Get aggregated M+ benchmark data from top players for a dungeon.

    Returns per-segment benchmarks (damage breakdown, major CD usage,
    defensive patterns, interrupt counts) from top 5 players at the
    specified key level. Segments are boss-bounded (trash between bosses
    is merged). Use this to understand what top players do in each part
    of the dungeon. Cost: ~25-35 WCL points (5 reports x 5-7 queries).

    Args:
        spec: Class specialization slug (e.g., "balance-druid")
        encounter_id: Dungeon encounter ID from get_encounters
        key_level: Mythic+ keystone level to benchmark (e.g., 10, 12)

    Returns:
        Benchmark bundle with segments (damage, CDs, defensives, interrupts)
        and CD spacing pattern across the full dungeon.
    """
    client = _get_wcl_client()
    result = await _get_mplus_benchmarks(client, spec, encounter_id, key_level)
    return result.model_dump()


# ============================================================
# 工具: get_cast_sequence
# ============================================================


@mcp.tool()
async def get_cast_sequence(
    report: str,
    fight_id: int,
    player: str,
    spec: str,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> dict:
    """
    Extract a player's cast sequence from a specific WCL fight.

    Returns a chronological list of cast events with spell names and
    timestamps (relative to fight start in seconds). Supports time
    range filtering. Useful for analyzing opener, burst windows, or
    specific fight phases in detail.

    Accepts a report code or full WCL URL. Cost: ~3-5 WCL points.

    Args:
        report: WCL report code or full URL
        fight_id: Fight ID within the report
        player: Character name (case-insensitive)
        spec: Class spec slug, e.g. "balance-druid"
        time_start: Start time in seconds relative to fight start (0 = beginning)
        time_end: End time in seconds relative to fight start (0 = end of fight)
    """
    client = _get_wcl_client()
    result = await _get_cast_sequence(
        client,
        report=report,
        fight_id=fight_id,
        player=player,
        spec=spec,
        time_start=time_start,
        time_end=time_end,
    )
    return result.model_dump()


# ============================================================
# 工具: get_buff_timeline
# ============================================================


@mcp.tool()
async def get_buff_timeline(
    report: str,
    fight_id: int,
    player: str,
    buff_ids: Optional[list[int]] = None,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> dict:
    """
    Get a player's buff event timeline from a specific WCL fight.

    Returns apply/remove/stack-change/refresh events for buffs and
    debuffs (DoTs), with uptime percentages and average stack counts.
    Filter by specific spell IDs or get all. Useful for analyzing
    Eclipse uptime, cooldown buffs, proc uptime, and DoT refresh
    timing (e.g. Moonfire/Sunfire clipping).

    Accepts a report code or full WCL URL. Cost: ~5-7 WCL points.

    Args:
        report: WCL report code or full URL
        fight_id: Fight ID within the report
        player: Character name (case-insensitive)
        buff_ids: Optional list of buff spell IDs to filter (None = all buffs)
        time_start: Start time in seconds relative to fight start (0 = beginning)
        time_end: End time in seconds relative to fight start (0 = end of fight)
    """
    client = _get_wcl_client()
    result = await _get_buff_timeline(
        client,
        report=report,
        fight_id=fight_id,
        player=player,
        buff_ids=buff_ids,
        time_start=time_start,
        time_end=time_end,
    )
    return result.model_dump()


# ============================================================
# 工具: get_resource_timeline
# ============================================================


@mcp.tool()
async def get_resource_timeline(
    report: str,
    fight_id: int,
    player: str,
    resource_type: str = "astral_power",
) -> dict:
    """
    Get a player's resource value timeline from a specific WCL fight.

    Tracks resource values (e.g., Astral Power, Rage, Energy) over
    time by extracting classResources from cast events. Detects
    resource overflow (capping). Useful for diagnosing resource waste.

    Supported resource types: astral_power, mana, rage, focus, energy,
    combo_points, runes, runic_power, soul_shards, holy_power,
    maelstrom, chi, insanity, fury, pain, essence.

    Accepts a report code or full WCL URL. Cost: ~3-5 WCL points.

    Args:
        report: WCL report code or full URL
        fight_id: Fight ID within the report
        player: Character name (case-insensitive)
        resource_type: Resource type string (default "astral_power")
    """
    client = _get_wcl_client()
    result = await _get_resource_timeline(
        client,
        report=report,
        fight_id=fight_id,
        player=player,
        resource_type=resource_type,
    )
    return result.model_dump()


# ============================================================
# 工具: get_coaching_context
# ============================================================


@mcp.tool()
async def get_coaching_context() -> dict:
    """
    Return coaching session context: user preferences, rate limit guidance,
    workflow tips, and known limitations. Call this FIRST in every new
    coaching conversation — zero API cost, provides essential context
    for effective coaching.
    """
    return await _get_coaching_context()


# ============================================================
# 工具: get_boss_cast_timeline
# ============================================================


@mcp.tool()
async def get_boss_cast_timeline(
    report: str,
    fight_id: int,
    spell_ids: list[int] | None = None,
) -> dict:
    """
    Query boss ability cast timeline for a specific fight.

    Returns timestamps of all enemy (boss) casts, useful for checking
    whether player cooldowns align with key mechanics (add spawns,
    vulnerability phases, etc.).

    If spell_ids are provided, only those abilities are queried.
    Otherwise, all known boss abilities for the encounter are returned.

    Args:
        report: WCL report code or full URL
        fight_id: Fight ID within the report
        spell_ids: Optional list of specific spell IDs to query

    Cost: ~1-3 WCL API points (depends on number of abilities queried)
    """
    client = _get_wcl_client()
    result = await _get_boss_cast_timeline(
        client,
        report=report,
        fight_id=fight_id,
        spell_ids=spell_ids,
    )
    return result.model_dump()


# ============================================================
# 入口
# ============================================================


def main() -> None:
    """启动 MCP 服务器（stdio 传输）。"""
    logger.info("WoW Coach MCP 服务器启动中...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
