"""
Pydantic 数据模型 — WCL 数据结构定义。

覆盖范围:
  - get_encounters: Encounter, Zone, EncountersResponse
  - get_top_builds: TalentBuild, TrinketInfo, StatProfile, TopBuildsResponse
  - get_cooldown_timelines (Phase 3): CastCluster, AbilityTimeline, CooldownTimelineResponse
  - get_rotation_profile (Phase 4): SpellStats, BuffUptime, RotationProfileResponse
  - analyze_player_log (Phase 5): SpellGap, CooldownIssue, DefensiveIssue, BuildDivergence, PlayerAnalysisResponse
  - analyze_dungeon_run (Phase 8): FightSegmentSummary, DungeonRunAnalysisResponse
  - M+ 基准数据 (Phase 8): MplusRankingEntry, MplusBenchmarkMeta
  - M+ benchmark aggregation (Phase 9): SegmentDamageBreakdown, SegmentCDCast, MplusBenchmarkSegment, MplusBenchmarkResponse
  - M+ comparison engine (Phase 10): SegmentDamageGap, SegmentComparison, BossCastComparison, DeathBreakdown, MplusComparisonResponse
  - M+ coaching tool (Phase 11): CoachingItem, SegmentCoaching, DungeonCoachingSummary, MplusCoachingResponse
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


# ============================================================
# Phase 6A: Downtime/GCD 分析
# ============================================================


class DowntimeWindow(BaseModel):
    """单个停工时间窗口。"""

    start_sec: float
    end_sec: float
    duration_sec: float


class DowntimeAnalysis(BaseModel):
    """停工/GCD 分析结果。"""

    active_time_pct: float = Field(description="玩家活跃时间百分比")
    benchmark_active_time_pct: float = Field(description="基准活跃时间百分比")
    total_downtime_sec: float = Field(description="总停工时间（秒）")
    downtime_windows: list[DowntimeWindow] = Field(default_factory=list)
    verdict: str = ""  # "ok", "low_activity", "very_low_activity"


# ============================================================
# Phase 6B: CD 窗口事件关联
# ============================================================


class CooldownWindowDetail(BaseModel):
    """单个冷却技能 Buff 窗口的施法密度分析。"""

    buff_name: str
    buff_spell_id: int
    start_sec: float
    end_sec: float
    duration_sec: float
    casts_during: int
    density_pct: float = Field(description="实际施法 / 理论最大 GCD 占比")


class EventLinkingAnalysis(BaseModel):
    """CD 窗口事件关联分析结果。"""

    cooldown_windows: list[CooldownWindowDetail] = Field(default_factory=list)
    low_density_windows_count: int = 0
    verdict: str = ""  # "ok", "low_density_burst"


# ============================================================
# Phase 6C: 天赋技能使用分析
# ============================================================


class TalentUsageGap(BaseModel):
    """单个天赋授予技能的使用差距分析。"""

    talent_name: str
    talent_id: int
    spell_name: str
    spell_id: int
    player_casts: int
    benchmark_median_casts: float
    player_cpm: float
    benchmark_cpm: float
    verdict: str = ""  # "unused", "underused", "ok"


class TalentUsageAnalysis(BaseModel):
    """天赋技能使用分析结果。"""

    talent_gaps: list[TalentUsageGap] = Field(default_factory=list)
    unused_talent_spells: list[str] = Field(default_factory=list)


# ============================================================
# Phase 6D: CD 窗口输出分析
# ============================================================


class CDWindowThroughput(BaseModel):
    """单个 CD 窗口的输出分析。"""

    ability_name: str
    window_index: int
    damage_done: float
    casts_during: int
    active_time_pct: float
    benchmark_median_damage: float
    benchmark_median_casts: float = 0.0
    verdict: str = ""  # "strong", "average", "weak"


# ============================================================
# Phase 6E: APL 循环检查
# ============================================================


class APLViolation(BaseModel):
    """单次 APL 违规事件。"""

    timestamp_sec: float
    expected_spell: str
    actual_spell: str
    rule_priority: int
    severity: str = ""  # "high", "medium", "low"
    benchmark_weight: float = 0.0


class APLAnalysis(BaseModel):
    """APL 循环合规性分析结果。"""

    spec: str
    apl_version: str = ""
    compliance_pct: float = 0.0
    violations: list[APLViolation] = Field(default_factory=list)
    high_severity_count: int = 0
    top_violation_patterns: list[str] = Field(default_factory=list)


# ============================================================
# Boss 技能时间线（get_boss_cast_timeline）
# ============================================================


class BossCastEvent(BaseModel):
    """单次 boss 施法事件。"""

    spell_id: int
    spell_name: str
    timestamp_sec: float = Field(description="相对于战斗开始的秒数")


class BossCastTimelineResponse(BaseModel):
    """get_boss_cast_timeline 工具返回值。"""

    report_code: str
    fight_id: int
    encounter_id: int = 0
    encounter_name: str = ""
    fight_duration: float = 0.0
    events: list[BossCastEvent] = Field(default_factory=list)
    spell_summary: dict[str, int] = Field(
        default_factory=dict,
        description="每个技能的施法次数: spell_name → count",
    )


class PlayerGearItem(BaseModel):
    """玩家装备栏单件装备。"""

    slot: int = Field(description="装备栏位 (0-17)")
    item_id: int = 0
    name: str = ""
    item_level: int = 0
    quality: int = 0


class PrepullBuff(BaseModel):
    """开战时的一个 buff（含消耗品、职业 buff 等）。"""

    ability_id: int
    name: str = ""
    stacks: int = 1


class PlayerCombatStats(BaseModel):
    """玩家战斗属性面板（从 CombatantInfo 提取）。"""

    stamina: int = 0
    intellect: int = 0
    strength: int = 0
    agility: int = 0
    crit: float = 0.0
    haste: float = 0.0
    mastery: float = 0.0
    versatility: float = 0.0
    leech: float = 0.0
    avoidance: float = 0.0
    speed: float = 0.0


class PlayerAnalysisResponse(BaseModel):
    """analyze_player_log 工具返回值 — 完整的玩家日志分析。"""

    report_code: str
    fight_id: int
    player_name: str
    spec: str
    encounter_id: int = 0
    encounter_name: str = ""
    difficulty: str = ""
    item_level: float = 0.0
    player_dps: float = 0.0
    dps_percentile: str = ""
    fight_duration: float = 0.0
    player_deaths: int = 0
    death_times: list[float] = []
    rotation_gaps: list[SpellGap] = []
    cooldown_issues: list[CooldownIssue] = []
    defensive_issues: list[DefensiveIssue] = []
    player_gear: list[PlayerGearItem] = Field(
        default_factory=list,
        description="玩家装备栏（含装等、饰品等）",
    )
    prepull_buffs: list[PrepullBuff] = Field(
        default_factory=list,
        description="开战时的 buff 列表（精炼药剂、食物、增强符文等）",
    )
    combat_stats: Optional[PlayerCombatStats] = None
    player_talents: list[str] = Field(
        default_factory=list,
        description="Player's full talent list with bilingual names",
    )
    build_divergence: BuildDivergence = Field(
        default_factory=BuildDivergence
    )
    cd_window_analysis: Optional[EventLinkingAnalysis] = None
    talent_usage: Optional[TalentUsageAnalysis] = None
    downtime: Optional[DowntimeAnalysis] = None
    cd_throughput: list[CDWindowThroughput] = Field(default_factory=list)
    apl_analysis: Optional[APLAnalysis] = None
    eclipse_metrics: Optional[EclipseMetrics] = None
    top_issues: list[str] = []


# ============================================================
# Phase 7: get_cast_sequence 相关
# ============================================================


class CastEvent(BaseModel):
    """单次施法事件。"""

    spell_id: int
    spell_name: str
    timestamp_sec: float = Field(description="相对于战斗开始的秒数")
    resource_amount: Optional[float] = Field(
        default=None, description="施法时的资源值（如星界能量），无数据时为 None"
    )
    resource_max: Optional[float] = Field(
        default=None, description="资源最大值，无数据时为 None"
    )


class CastSequenceResponse(BaseModel):
    """get_cast_sequence 工具返回值。"""

    report_code: str
    fight_id: int
    player_name: str
    spec: str
    fight_duration: float = 0.0
    time_start: float = 0.0
    time_end: float = 0.0
    total_casts: int = 0
    casts: list[CastEvent] = Field(default_factory=list)


# ============================================================
# Phase 7: get_buff_timeline 相关
# ============================================================


class BuffEvent(BaseModel):
    """单次 Buff 事件（apply/remove/stack 变化）。"""

    buff_id: int
    buff_name: str
    event_type: str = Field(description="applybuff, removebuff, applybuffstack, removebuffstack")
    timestamp_sec: float = Field(description="相对于战斗开始的秒数")
    stacks: int = 0


class BuffSummary(BaseModel):
    """单个 Buff 的时间线统计。"""

    buff_id: int
    buff_name: str
    uptime_pct: float = 0.0
    avg_stacks: float = 0.0
    apply_count: int = 0
    events: list[BuffEvent] = Field(default_factory=list)


class BuffTimelineResponse(BaseModel):
    """get_buff_timeline 工具返回值。"""

    report_code: str
    fight_id: int
    player_name: str
    fight_duration: float = 0.0
    time_start: float = 0.0
    time_end: float = 0.0
    buffs: list[BuffSummary] = Field(default_factory=list)


# ============================================================
# Phase 7: get_resource_timeline 相关
# ============================================================


class ResourcePoint(BaseModel):
    """资源变化事件（WCL resourcechange delta）。"""

    timestamp_sec: float = Field(description="相对于战斗开始的秒数")
    value: int = Field(default=0, description="资源变化量（正=获取，负=消耗）")
    max_value: int = Field(default=0, description="资源上限")
    spell_name: str = Field(default="", description="触发资源变化的技能名称")
    is_overflow: bool = Field(default=False, description="是否发生溢出（waste > 0）")


class ResourceTimelineResponse(BaseModel):
    """get_resource_timeline 工具返回值。"""

    report_code: str
    fight_id: int
    player_name: str
    resource_type: str
    fight_duration: float = 0.0
    total_points: int = 0
    overflow_count: int = 0
    overflow_pct: float = 0.0
    points: list[ResourcePoint] = Field(default_factory=list)


# ============================================================
# Phase 7: Eclipse 指标（Balance Druid 专用）
# ============================================================


class EclipseMetrics(BaseModel):
    """Balance Druid Eclipse 指标。"""

    eclipse_uptime_pct: float = 0.0
    avg_eclipse_gap_sec: float = 0.0
    ca_eclipse_coverage_pct: float = 0.0
    starlord_uptime_pct: float = 0.0


# ============================================================
# Phase 8: M+ 副本整体分析
# ============================================================


class FightSegmentSummary(BaseModel):
    """M+ 副本中单个战斗段落摘要。"""

    fight_id: int
    name: str
    is_boss: bool
    duration_sec: float
    player_dps: float
    deaths: int


class DungeonRunAnalysisResponse(BaseModel):
    """analyze_dungeon_run 工具返回值 — M+ 副本整体分析。"""

    report_code: str
    player_name: str
    spec: str
    dungeon_name: str = ""
    keystone_level: int = 0
    total_duration_sec: float = 0.0
    active_time_sec: float = 0.0
    total_dps: float = 0.0
    total_damage: float = 0.0
    total_deaths: int = 0
    death_times: list[float] = []
    damage_by_ability: list[dict] = Field(
        default_factory=list, description="[{name, total, pct}] 伤害技能排行（前15）"
    )
    buff_uptimes: list[dict] = Field(
        default_factory=list, description="[{name, uptime_pct}] Buff 覆盖率"
    )
    segments: list[FightSegmentSummary] = Field(default_factory=list)
    item_level: float = 0.0
    player_talents: list[str] = []
    spell_counts: dict[str, int] = Field(
        default_factory=dict, description="技能施法统计（仅 include_casts=True 时填充）"
    )
    active_time_pct: float = 0.0
    top_issues: list[str] = []


# ============================================================
# M+ 基准数据 (Phase 8)
# ============================================================


class MplusRankingEntry(BaseModel):
    """M+ 排行榜单条记录 — 从 characterRankings 解析。"""

    name: str
    class_name: str = Field(default="", alias="class")
    spec: str = ""
    amount: float = Field(description="DPS 数值")
    duration: int = Field(description="副本总时长（毫秒）")
    report_code: str = ""
    fight_id: int = 0
    bracket_data: int = Field(
        default=0,
        alias="bracketData",
        description="M+ 钥石等级（非装等）",
    )

    model_config = {"populate_by_name": True}


class MplusBenchmarkMeta(BaseModel):
    """M+ 基准元数据 — 记录基准构建参数。"""

    encounter_id: int
    encounter_name: str = ""
    spec: str
    key_level: int
    actual_bracket: int = Field(
        default=0,
        description="实际使用的 bracket（fallback 时可能与 key_level 不同）",
    )
    sample_size: int = 0
    median_dps: float = 0.0
    dps_p25: float = 0.0
    dps_p75: float = 0.0
    cached_at: str = ""


# ============================================================
# M+ Benchmark Aggregation (Phase 9)
# ============================================================


class SegmentDamageBreakdown(BaseModel):
    """段落内单个技能的伤害条目。"""

    spell_name: str
    spell_id: int = 0
    total_damage: float = 0.0
    damage_pct: float = 0.0


class SegmentCDCast(BaseModel):
    """段落内聚合的大技能施放。"""

    spell_name: str
    spell_id: int = 0
    cast_count_median: float = 0.0
    ability_type: str = ""


class MplusBenchmarkSegment(BaseModel):
    """单个 Boss 边界段落的基准数据。"""

    position: int
    segment_type: str = ""
    segment_name: str = ""
    duration_median: float = 0.0
    damage_breakdown: list[SegmentDamageBreakdown] = Field(default_factory=list)
    cd_casts: list[SegmentCDCast] = Field(default_factory=list)
    defensive_cds: list[SegmentCDCast] = Field(default_factory=list)
    interrupt_count_median: float = 0.0


class MplusBenchmarkResponse(BaseModel):
    """完整副本基准数据包 — get_mplus_benchmarks 工具返回值。"""

    meta: MplusBenchmarkMeta
    segments: list[MplusBenchmarkSegment] = Field(default_factory=list)
    cd_spacing: list[dict] = Field(
        default_factory=list,
        description="CD 在各段落的分布: [{spell_name, spell_id, segments: [position]}]",
    )


# ============================================================
# M+ Comparison Engine (Phase 10)
# ============================================================


class SegmentDamageGap(BaseModel):
    """段落内单个技能的伤害差距分析。"""

    spell_name: str
    spell_id: int = 0
    player_pct: float = 0.0
    benchmark_pct: float = 0.0
    gap_pct: float = 0.0
    flagged: bool = False


class SegmentComparison(BaseModel):
    """单个段落的完整对比结果。"""

    position: int
    segment_type: str = ""
    segment_name: str = ""
    status: str = ""
    damage_gaps: list[SegmentDamageGap] = Field(default_factory=list)
    cd_gaps: list[dict] = Field(default_factory=list)
    interrupt_comparison: dict = Field(default_factory=dict)


class BossCastComparison(BaseModel):
    """Boss 战斗的施法对比结果。"""

    boss_name: str
    position: int = 0
    player_duration_sec: float = 0.0
    benchmark_duration_sec: float = 0.0
    cast_gaps: list[dict] = Field(default_factory=list)
    cd_gaps: list[dict] = Field(default_factory=list)
    defensive_gaps: list[dict] = Field(default_factory=list)
    status: str = ""


class DeathBreakdown(BaseModel):
    """单次死亡事件的详细分析。"""

    death_time_sec: float = 0.0
    segment_position: int = 0
    segment_name: str = ""
    damage_taken_sources: list[dict] = Field(default_factory=list)
    defensive_status: list[dict] = Field(default_factory=list)


class MplusComparisonResponse(BaseModel):
    """compare_mplus_run 工具返回值 — M+ 副本完整对比。"""

    report_code: str
    player_name: str
    spec: str
    dungeon_name: str = ""
    key_level: int = 0
    benchmark_key_level: int = 0
    segment_comparisons: list[SegmentComparison] = Field(default_factory=list)
    boss_comparisons: list[BossCastComparison] = Field(default_factory=list)
    death_analysis: list[DeathBreakdown] = Field(default_factory=list)
    interrupt_summary: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)


# ============================================================
# M+ Coaching Tool (Phase 11)
# ============================================================


class CoachingItem(BaseModel):
    """单条教练建议 — 结构化数据 + 自然语言建议。"""

    category: str = ""          # "damage", "cooldown", "interrupt", "cast", "defensive", "death", "positive"
    spell_name: str = ""
    gap_pct: float = 0.0        # 差距百分比（正 = 玩家低于基准）
    player_value: float = 0.0
    benchmark_value: float = 0.0
    coaching_text: str = ""     # 自然语言建议


class SegmentCoaching(BaseModel):
    """单个段落的教练输出。"""

    position: int
    segment_type: str = ""      # "trash" or "boss"
    segment_name: str = ""
    items: list[CoachingItem] = Field(default_factory=list)


class DungeonCoachingSummary(BaseModel):
    """整个副本的教练汇总。"""

    total_damage_flags: int = 0
    total_cd_flags: int = 0
    total_deaths: int = 0
    total_interrupt_flags: int = 0
    top_improvements: list[CoachingItem] = Field(default_factory=list)
    overall_coaching_text: str = ""


class MplusCoachingResponse(BaseModel):
    """coach_mplus_run 工具返回值 — M+ 副本完整教练报告。"""

    report_code: str
    player_name: str
    spec: str
    dungeon_name: str = ""
    key_level: int = 0
    benchmark_key_level: int = 0
    segment_coaching: list[SegmentCoaching] = Field(default_factory=list)
    death_coaching: list[CoachingItem] = Field(default_factory=list)
    summary: DungeonCoachingSummary = Field(default_factory=DungeonCoachingSummary)
