"""
Pydantic 数据模型 — WCL 数据结构定义。

覆盖范围:
  - get_encounters: Encounter, Zone, EncountersResponse
  - get_top_builds: TalentBuild, TrinketInfo, StatProfile, TopBuildsResponse
  - get_cooldown_timelines (Phase 3): CastCluster, AbilityTimeline, CooldownTimelineResponse
  - get_rotation_profile (Phase 4): SpellStats, BuffUptime, RotationProfileResponse
  - analyze_player_log (Phase 5): SpellGap, CooldownIssue, DefensiveIssue, BuildDivergence, PlayerAnalysisResponse
  - 通用: RateLimitInfo

[PROTOCOL]: 变更时更新此文档，然后检查父级
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ============================================================
# 通用 / 速率限制
# ============================================================


class RateLimitInfo(BaseModel):
    """WCL API 速率限制状态。"""

    limit_per_hour: float = Field(alias="limitPerHour", default=0)
    points_spent_this_hour: float = Field(
        alias="pointsSpentThisHour", default=0
    )
    points_reset_in: float = Field(alias="pointsResetIn", default=0)

    model_config = {"populate_by_name": True}

    @property
    def points_remaining(self) -> float:
        return self.limit_per_hour - self.points_spent_this_hour


# ============================================================
# get_encounters 相关
# ============================================================


class Encounter(BaseModel):
    """副本 Boss / 地下城遭遇。"""

    id: int
    name: str


class Zone(BaseModel):
    """副本区域（团本或地下城）。"""

    id: int
    name: str
    encounters: list[Encounter] = []


class Expansion(BaseModel):
    """资料片。"""

    id: int
    name: str
    zones: list[Zone] = []


class EncountersResponse(BaseModel):
    """get_encounters 工具返回值。"""

    expansion: str
    zones: list[Zone]


# ============================================================
# get_top_builds 相关
# ============================================================


class TalentBuild(BaseModel):
    """天赋构建摘要。"""

    talent_import: str = Field(
        description="天赋导入字符串"
    )
    talent_summary: str = Field(
        default="",
        description="关键天赋的中文名称摘要",
    )
    usage_pct: float = Field(description="使用率百分比")
    player_count: int = Field(description="使用该构建的玩家数")


class FlexNode(BaseModel):
    """天赋弹性节点 — 构建分歧点。"""

    talent_name: str
    tree: str = Field(default="", description="天赋子树: class, spec, or hero")
    pick_rate: float = Field(description="选取率百分比")


class TrinketInfo(BaseModel):
    """饰品统计。"""

    name: str
    item_id: int = 0
    usage_pct: float = Field(description="使用率百分比")
    count: int = 0


class StatDistribution(BaseModel):
    """属性分布（中位数 / P25 / P75）。"""

    median: float = 0.0
    p25: float = 0.0
    p75: float = 0.0


class StatProfile(BaseModel):
    """四维属性分布。"""

    crit: StatDistribution = StatDistribution()
    haste: StatDistribution = StatDistribution()
    mastery: StatDistribution = StatDistribution()
    versatility: StatDistribution = StatDistribution()
    item_level: StatDistribution = StatDistribution()


class TopBuildsResponse(BaseModel):
    """get_top_builds 工具返回值。"""

    spec: str
    encounter_id: int
    encounter_name: str = ""
    difficulty: str
    sample_size: int = 0
    builds: list[TalentBuild] = []
    flex_nodes: list[FlexNode] = []
    top_trinkets: list[TrinketInfo] = []
    stat_profile: StatProfile = StatProfile()


# ============================================================
# get_cooldown_timelines 相关（Phase 3 预定义）
# ============================================================


class CoUsage(BaseModel):
    """共用技能统计。"""

    ability: str
    rate: float


class CastCluster(BaseModel):
    """一组时间接近的施法聚类。"""

    label: str = ""
    median_time: float
    std_dev: float = 0.0
    range: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    player_pct: float = 0.0
    phase: str = ""
    co_used: list[CoUsage] = []
    hold: Optional[dict[str, float]] = None


class AbilityTimeline(BaseModel):
    """单个技能的施法时间线聚合。"""

    name: str
    ability_type: str = Field(default="", alias="type")
    cd_seconds: float = 0.0
    total_casts: dict[str, float] = Field(default_factory=dict)
    cast_clusters: list[CastCluster] = []
    consensus: str = ""

    model_config = {"populate_by_name": True}


class BossPhase(BaseModel):
    """Boss 阶段时间区间。"""

    name: str
    typical_start: float = 0.0
    typical_end: float = 0.0


class CooldownTimelineResponse(BaseModel):
    """get_cooldown_timelines 工具返回值（Phase 3）。"""

    spec: str
    encounter: str
    difficulty: str = "heroic"
    sample_size: int = 0
    median_fight_duration: float = 0.0
    boss_phases: list[BossPhase] = []
    abilities: list[AbilityTimeline] = []


# ============================================================
# get_rotation_profile 相关（Phase 4）
# ============================================================


class SpellStats(BaseModel):
    """单个技能的循环统计。"""

    name: str
    spell_id: int
    total_casts: float = Field(description="玩家中位数施法次数")
    cpm: float = Field(description="每分钟施法次数中位数")
    percentiles: dict[str, float] = Field(
        default_factory=dict,
        description="施法次数百分位 p25/p50/p75",
    )


class BuffUptime(BaseModel):
    """Buff 覆盖率统计。"""

    name: str
    spell_id: int
    uptime_pct: float = Field(description="中位数覆盖率百分比")


class RotationProfileResponse(BaseModel):
    """get_rotation_profile 工具返回值（Phase 4）。"""

    spec: str
    encounter_id: int
    encounter_name: str = ""
    difficulty: str
    sample_size: int = 0
    fight_duration_median: float = Field(
        default=0.0, description="中位数战斗时长（秒）"
    )
    top_spells: list[SpellStats] = Field(
        default_factory=list,
        description="按施法次数降序排列的前 15 个技能",
    )
    buff_uptimes: list[BuffUptime] = Field(
        default_factory=list,
        description="按覆盖率降序排列的 Buff 列表",
    )
    dps_median: float = 0.0
    dps_p25: float = 0.0
    dps_p75: float = 0.0


# ============================================================
# analyze_player_log 相关（Phase 5 — 玩家日志分析）
# ============================================================


class SpellGap(BaseModel):
    """循环中单个技能的差距分析。"""

    name: str
    spell_id: int
    player_casts: int
    player_cpm: float
    benchmark_median: float
    benchmark_cpm: float
    percentile: str = ""  # "below_p25", "p25_p50", "p50_p75", "above_p75"
    verdict: str = ""     # "undercast", "ok", "overcast"


class CooldownIssue(BaseModel):
    """冷却技能使用差距。"""

    name: str
    spell_id: int
    player_casts: int
    benchmark_median_casts: float
    missed_uses: int = 0


class DefensiveIssue(BaseModel):
    """防御技能使用差距。"""

    name: str
    spell_id: int
    player_used: bool
    player_cast_count: int = 0
    benchmark_usage_rate: float = 0.0
    verdict: str = ""  # "unused", "underused", "ok"


class BuildDivergence(BaseModel):
    """天赋构建对比结果。"""

    best_match_build: int = 0
    similarity_pct: float = 0.0
    missing_meta_talents: list[str] = []
    extra_talents: list[str] = []


class PlayerAnalysisResponse(BaseModel):
    """analyze_player_log 工具返回值 — 完整的玩家日志分析。"""

    report_code: str
    fight_id: int
    player_name: str
    spec: str
    encounter_id: int = 0
    encounter_name: str = ""
    difficulty: str = ""
    player_dps: float = 0.0
    dps_percentile: str = ""
    fight_duration: float = 0.0
    player_deaths: int = 0
    death_times: list[float] = []
    rotation_gaps: list[SpellGap] = []
    cooldown_issues: list[CooldownIssue] = []
    defensive_issues: list[DefensiveIssue] = []
    player_talents: list[str] = Field(
        default_factory=list,
        description="Player's full talent list with bilingual names",
    )
    build_divergence: BuildDivergence = Field(
        default_factory=BuildDivergence
    )
    top_issues: list[str] = []
