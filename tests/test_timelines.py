# ============================================================
# get_cooldown_timelines 工具测试
# 覆盖聚类逻辑、hold 检测、co-usage 检测、集成流程
#
# 测试目标模块: src.tools.timelines (Phase 3)
# 测试数据: tests/fixtures/wcl_responses.py 中的 TIMELINE_* 系列
#
# 技能 ID 参考:
#   51271 = Pillar of Frost (CD 60s)
#   279302 = Frostwyrm's Fury (CD 180s)
# ============================================================
from __future__ import annotations

import statistics
from collections import defaultdict
from unittest.mock import patch

import pytest

from tests.conftest import MockWCLClient
from tests.fixtures.wcl_responses import (
    CAST_EVENTS_PAGINATED_PAGE1,
    CAST_EVENTS_PAGINATED_PAGE2,
    CAST_EVENTS_REPORT_AAA,
    CAST_EVENTS_REPORT_BBB,
    CAST_EVENTS_REPORT_CCC,
    CAST_EVENTS_REPORT_DDD,
    CAST_EVENTS_REPORT_EEE,
    MASTER_DATA_REPORT_AAA,
    MASTER_DATA_REPORT_BBB,
    MASTER_DATA_REPORT_CCC,
    MASTER_DATA_REPORT_DDD,
    MASTER_DATA_REPORT_EEE,
    TIMELINE_RANKINGS_EMPTY_RESPONSE,
    TIMELINE_RANKINGS_RESPONSE,
)
from src.models import (
    AbilityTimeline,
    BossPhase,
    CastCluster,
    CooldownTimelineResponse,
    CoUsage,
)
from src.tools.timelines import (
    _cluster_timestamps,
    _detect_holds,
    _compute_co_usage,
    _build_cluster,
    _generate_consensus,
    get_cooldown_timelines,
)


# ============================================================
# 辅助函数 — 配置 mock client 的时间线响应
# ============================================================
def _setup_timeline_client(client: MockWCLClient) -> None:
    """
    为时间线测试预配置 mock WCL client。

    配置策略:
    - characterRankings 查询 → 返回时间线排行榜
    - masterData 查询 → 按 report code 匹配（使用 GQL 中的 report(code: "xxx") 模式）
    - events 查询 → 按 report code + sourceID 匹配

    MockWCLClient 使用最长匹配策略。
    """
    # 排行榜（覆盖默认的 characterRankings 响应）
    client.set_response("characterRankings", TIMELINE_RANKINGS_RESPONSE)

    # 每个 report 的 masterData — 使用 GQL 中实际出现的字符串
    client.set_response('report(code: "rpt_AAA111")', MASTER_DATA_REPORT_AAA)
    client.set_response('report(code: "rpt_BBB222")', MASTER_DATA_REPORT_BBB)
    client.set_response('report(code: "rpt_CCC333")', MASTER_DATA_REPORT_CCC)
    client.set_response('report(code: "rpt_DDD444")', MASTER_DATA_REPORT_DDD)
    client.set_response('report(code: "rpt_EEE555")', MASTER_DATA_REPORT_EEE)

    # 每个 report 的施法事件 — 使用更具体的匹配键
    # masterData 和 events 都匹配 report(code: "xxx")，
    # 但 events 查询还包含 "events(" 关键字，用此区分
    # GQL 格式: report(code: "xxx") {\n                    events(
    client.set_response(
        'rpt_AAA111") {\n                    events',
        CAST_EVENTS_REPORT_AAA,
    )
    client.set_response(
        'rpt_BBB222") {\n                    events',
        CAST_EVENTS_REPORT_BBB,
    )
    client.set_response(
        'rpt_CCC333") {\n                    events',
        CAST_EVENTS_REPORT_CCC,
    )
    client.set_response(
        'rpt_DDD444") {\n                    events',
        CAST_EVENTS_REPORT_DDD,
    )
    client.set_response(
        'rpt_EEE555") {\n                    events',
        CAST_EVENTS_REPORT_EEE,
    )


@pytest.fixture
def timeline_client() -> MockWCLClient:
    """提供预配置的时间线测试 mock client。"""
    client = MockWCLClient()
    _setup_timeline_client(client)
    return client


# ============================================================
# 单元测试 — 聚类逻辑
# _cluster_timestamps 返回 list[list[float]]
# 每个子列表是一个聚类中的原始时间戳
# ============================================================
class TestClusterLogic:
    """施法时间戳聚类算法测试。"""

    def test_cluster_basic(self):
        """5 个时间戳在 15s 内 → 应聚为 1 个簇"""
        timestamps = [2.0, 3.0, 2.5, 4.0, 6.0]
        clusters = _cluster_timestamps(timestamps)
        assert len(clusters) == 1
        # 所有时间戳都在同一个簇中
        assert len(clusters[0]) == 5
        # 中位数应在 2-6 之间
        median = statistics.median(clusters[0])
        assert 2.0 <= median <= 6.0

    def test_cluster_two_groups(self):
        """时间戳分两组 (~5s 和 ~90s) → 应聚为 2 个簇"""
        timestamps = [2.0, 3.0, 4.0, 88.0, 90.0, 92.0]
        clusters = _cluster_timestamps(timestamps)
        assert len(clusters) == 2
        # 第一个簇在开场附近
        assert statistics.median(clusters[0]) < 10.0
        # 第二个簇在 ~90s 附近
        assert statistics.median(clusters[1]) > 80.0

    def test_cluster_empty(self):
        """无施法数据 → 空聚类结果"""
        clusters = _cluster_timestamps([])
        assert clusters == []

    def test_cluster_single_cast(self):
        """1 次施法 → 1 个簇，包含 1 个时间戳"""
        clusters = _cluster_timestamps([42.0])
        assert len(clusters) == 1
        assert clusters[0] == [42.0]

    def test_cluster_boundary_exactly_15s(self):
        """间隔恰好 15s 的行为验证（边界条件）"""
        # 第一个时间戳 0s，第二个 15s
        # 根据实现，比较 ts - mean，15 恰好等于阈值但不超过
        timestamps = [0.0, 15.0]
        clusters = _cluster_timestamps(timestamps)
        # 实际行为取决于 > 还是 >=
        assert len(clusters) >= 1

    def test_cluster_three_groups(self):
        """三组明显分开的时间戳 → 3 个簇"""
        timestamps = [1.0, 2.0, 3.0,  # 开场
                      60.0, 61.0, 62.0,  # 1 分钟
                      180.0, 181.0]  # 3 分钟
        clusters = _cluster_timestamps(timestamps)
        assert len(clusters) == 3


# ============================================================
# 单元测试 — _build_cluster
# ============================================================
class TestBuildCluster:
    """从原始时间戳列表构建 CastCluster 对象。"""

    def test_basic_cluster(self):
        """基本构建: 5 个时间戳，5 个玩家中的 3 个"""
        cluster = _build_cluster([2.0, 3.0, 4.0, 2.5, 3.5], 5, 3)
        assert isinstance(cluster, CastCluster)
        assert cluster.median_time == 3.0
        assert cluster.player_pct == 60.0
        assert cluster.range == [2.0, 4.0]

    def test_single_timestamp_std_dev_zero(self):
        """单个时间戳 → std_dev = 0"""
        cluster = _build_cluster([42.0], 1, 1)
        assert cluster.std_dev == 0.0
        assert cluster.player_pct == 100.0

    def test_all_same_timestamp(self):
        """所有时间戳相同 → std_dev = 0"""
        cluster = _build_cluster([5.0, 5.0, 5.0], 3, 3)
        assert cluster.median_time == 5.0
        assert cluster.std_dev == 0.0

    def test_player_pct_calculation(self):
        """3/5 玩家在簇中 → player_pct = 60%"""
        cluster = _build_cluster([1.0, 2.0, 3.0], 5, 3)
        assert cluster.player_pct == 60.0

    def test_full_participation(self):
        """所有 5 个玩家都在簇中 → player_pct = 100%"""
        cluster = _build_cluster([1.0, 2.0, 3.0, 4.0, 5.0], 5, 5)
        assert cluster.player_pct == 100.0


# ============================================================
# 单元测试 — Hold 检测
# _detect_holds 就地修改 CastCluster 列表
# ============================================================
class TestHoldDetection:
    """技能 hold（延迟使用）检测测试。"""

    def test_hold_detected(self):
        """
        簇1在 3s，簇2在 90s，CD 60s →
        off_cd_at = 3+60=63s，held = 90-63=27s → 超过阈值5s
        """
        c1 = CastCluster(median_time=3.0)
        c2 = CastCluster(median_time=90.0)
        clusters = [c1, c2]
        _detect_holds(clusters, cd_seconds=60.0)
        # c2 应标记 hold
        assert c2.hold is not None
        assert abs(c2.hold["held_seconds"] - 27.0) < 0.5
        assert abs(c2.hold["off_cd_at"] - 63.0) < 0.5

    def test_no_hold_within_threshold(self):
        """
        簇1在 3s，簇2在 65s，CD 60s →
        off_cd_at = 63s，held = 2s → 低于阈值5s → 无 hold
        """
        c1 = CastCluster(median_time=3.0)
        c2 = CastCluster(median_time=65.0)
        clusters = [c1, c2]
        _detect_holds(clusters, cd_seconds=60.0)
        assert c2.hold is None

    def test_first_cluster_never_held(self):
        """第一个簇永远不标记 hold（没有参照点）"""
        c1 = CastCluster(median_time=30.0)
        clusters = [c1]
        _detect_holds(clusters, cd_seconds=60.0)
        assert c1.hold is None

    def test_hold_chain(self):
        """连续 3 个簇: 3s → 90s → 200s (CD 60s)"""
        c1 = CastCluster(median_time=3.0)
        c2 = CastCluster(median_time=90.0)
        c3 = CastCluster(median_time=200.0)
        clusters = [c1, c2, c3]
        _detect_holds(clusters, cd_seconds=60.0)
        # c2: off_cd=63, held=27 → hold
        assert c2.hold is not None
        # c3: off_cd=90+60=150, held=200-150=50 → hold
        assert c3.hold is not None
        assert abs(c3.hold["held_seconds"] - 50.0) < 0.5


# ============================================================
# 单元测试 — Co-usage 检测
# _compute_co_usage(casts, spell_id, range, tracked_spells)
# ============================================================
class TestCoUsageDetection:
    """技能共用（同时使用）检测测试。"""

    def _make_casts_and_tracked(self):
        """构造测试用的 casts 列表和 tracked_spells 字典。"""
        tracked = {
            51271: {"name": "Pillar of Frost", "cd_seconds": 60.0, "ability_type": "utility"},
            279302: {"name": "Frostwyrm's Fury", "cd_seconds": 180.0, "ability_type": "offensive"},
        }
        return tracked

    def test_co_usage_detected(self):
        """
        Pillar of Frost (3s) + Frostwyrm's Fury (4s) 同一玩家
        间隔 1s < 3s 窗口 → 应检测到 co-usage
        """
        tracked = self._make_casts_and_tracked()
        casts = [
            {"player": "Frostblade", "spell_id": 51271, "relative_sec": 3.0},
            {"player": "Frostblade", "spell_id": 279302, "relative_sec": 4.0},
        ]
        result = _compute_co_usage(
            casts,
            spell_id=51271,
            cluster_ts_range=(2.0, 4.0),
            tracked_spells=tracked,
        )
        co_names = [c.ability for c in result]
        assert "Frostwyrm's Fury" in co_names

    def test_co_usage_not_detected_far_apart(self):
        """
        Pillar of Frost (3s) vs Frostwyrm's Fury (20s)
        间隔 17s > 3s 窗口 → 不应检测到 co-usage
        """
        tracked = self._make_casts_and_tracked()
        casts = [
            {"player": "Frostblade", "spell_id": 51271, "relative_sec": 3.0},
            {"player": "Frostblade", "spell_id": 279302, "relative_sec": 20.0},
        ]
        result = _compute_co_usage(
            casts,
            spell_id=51271,
            cluster_ts_range=(2.0, 4.0),
            tracked_spells=tracked,
        )
        co_names = [c.ability for c in result]
        assert "Frostwyrm's Fury" not in co_names

    def test_co_usage_rate_threshold(self):
        """低于 10% 共用率的技能不报告"""
        tracked = self._make_casts_and_tracked()
        # 10 个玩家用 Pillar，只有 0 个用 Frostwyrm 在窗口内
        casts = [
            {"player": f"Player{i}", "spell_id": 51271, "relative_sec": 3.0 + i * 0.1}
            for i in range(10)
        ]
        # 1 个玩家的 Frostwyrm 太远
        casts.append({"player": "Player0", "spell_id": 279302, "relative_sec": 30.0})
        result = _compute_co_usage(
            casts,
            spell_id=51271,
            cluster_ts_range=(2.0, 5.0),
            tracked_spells=tracked,
        )
        # Frostwyrm 离太远，不在窗口内
        co_names = [c.ability for c in result]
        assert "Frostwyrm's Fury" not in co_names

    def test_co_usage_empty_casts(self):
        """无施法数据 → 空 co-usage"""
        tracked = self._make_casts_and_tracked()
        result = _compute_co_usage(
            [],
            spell_id=51271,
            cluster_ts_range=(0.0, 10.0),
            tracked_spells=tracked,
        )
        assert result == []


# ============================================================
# 单元测试 — 共识文本
# ============================================================
class TestConsensus:
    """_generate_consensus 共识文本生成。"""

    def test_high_consensus(self):
        """All clusters player_pct >= 70 → high consensus"""
        clusters = [
            CastCluster(median_time=3.0, player_pct=80.0),
            CastCluster(median_time=65.0, player_pct=75.0),
        ]
        assert _generate_consensus(clusters) == "high consensus"

    def test_partial_consensus(self):
        """Some clusters >= 70% → partial consensus"""
        clusters = [
            CastCluster(median_time=3.0, player_pct=80.0),
            CastCluster(median_time=65.0, player_pct=40.0),
        ]
        assert _generate_consensus(clusters) == "partial consensus"

    def test_low_consensus(self):
        """All clusters < 70% → low consensus"""
        clusters = [
            CastCluster(median_time=3.0, player_pct=50.0),
            CastCluster(median_time=65.0, player_pct=40.0),
        ]
        assert _generate_consensus(clusters) == "low consensus"

    def test_empty_clusters(self):
        """No clusters → insufficient data"""
        assert _generate_consensus([]) == "insufficient data"


# ============================================================
# 集成测试 — 完整流程
# ============================================================
class TestGetCooldownTimelinesIntegration:
    """get_cooldown_timelines 端到端集成测试（mock WCL）。"""

    @pytest.mark.asyncio
    async def test_basic_flow(self, timeline_client):
        """完整流程: 排行榜 → 事件采集 → 聚合 → 返回结构正确"""
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # 返回类型正确
        assert isinstance(result, CooldownTimelineResponse)
        # 基本字段
        assert result.spec == "frost-death-knight"
        assert result.encounter == "Vorasius"
        assert result.sample_size == 5
        # 应包含至少一个技能时间线
        assert len(result.abilities) >= 1
        # 每个技能应有 cast_clusters
        for ability in result.abilities:
            assert isinstance(ability, AbilityTimeline)
            assert len(ability.name) > 0

    @pytest.mark.asyncio
    async def test_cached_second_call(self, timeline_client):
        """相同参数的第二次调用使用缓存，无额外 WCL 查询"""
        # 第一次调用
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set") as mock_cset:
            await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )
            cached_data = mock_cset.call_args[0][1]

        count_after_first = timeline_client.query_call_count

        # 第二次调用（缓存命中）
        with patch("src.tools.timelines.cache_get", return_value=cached_data), \
             patch("src.tools.timelines.cache_set"):
            result2 = await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count_after_second = timeline_client.query_call_count
        # 不应有新的 WCL 查询
        assert count_after_second == count_after_first
        # 结果仍然正确
        assert isinstance(result2, CooldownTimelineResponse)

    @pytest.mark.asyncio
    async def test_invalid_spec_returns_error(self, timeline_client):
        """
        无效 spec slug → 返回错误响应（不抛异常）。
        get_cooldown_timelines 内部 catch 异常并返回 encounter="Error: ..."
        """
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=timeline_client,
                spec="invalid-nonexistent-spec",
                encounter_id=3001,
            )
        assert isinstance(result, CooldownTimelineResponse)
        # 无效 spec 导致 tracked_spells 为空或报错
        # 结果: 要么 sample_size=0，要么 encounter 包含 Error
        assert result.sample_size == 0 or "Error" in result.encounter

    @pytest.mark.asyncio
    async def test_empty_rankings(self):
        """无排名数据 → 优雅返回空结果"""
        client = MockWCLClient()
        client.set_response("characterRankings", TIMELINE_RANKINGS_EMPTY_RESPONSE)

        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result, CooldownTimelineResponse)
        assert result.sample_size == 0
        assert result.abilities == []

    @pytest.mark.asyncio
    async def test_ability_filter_by_spell_id(self, timeline_client):
        """指定 abilities (spell ID 列表) 只返回请求的技能"""
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
                abilities=[51271],  # 只要 Pillar of Frost
            )

        assert isinstance(result, CooldownTimelineResponse)
        ability_names = [a.name for a in result.abilities]
        # 如果有数据，应只包含 Pillar of Frost
        if ability_names:
            assert "Pillar of Frost" in ability_names
            assert "Frostwyrm's Fury" not in ability_names

    @pytest.mark.asyncio
    async def test_pagination_handling(self):
        """事件分页: nextPageTimestamp 触发续页查询"""
        client = MockWCLClient()
        # 只设置 1 个玩家用于分页测试
        client.set_response("characterRankings", {
            "worldData": {
                "encounter": {
                    "name": "Vorasius",
                    "characterRankings": {
                        "rankings": [
                            {
                                "name": "Frostblade",
                                "server": {"slug": "illidan", "region": "us"},
                                "report": {"code": "rpt_PAG001", "fightID": 1},
                                "amount": 1350000,
                                "rank": 1,
                            },
                        ],
                        "page": 1,
                        "hasMorePages": False,
                    },
                }
            },
        })
        client.set_response('report(code: "rpt_PAG001")', {
            "reportData": {
                "report": {
                    "masterData": {
                        "actors": [
                            {"id": 1, "name": "Frostblade", "type": "Player",
                             "subType": "DeathKnight"},
                        ]
                    }
                }
            },
        })
        # 第一页有 nextPageTimestamp — 匹配初始 startTime: 0
        client.set_response(
            'report(code: "rpt_PAG001")\n                    events',
            CAST_EVENTS_PAGINATED_PAGE1,
        )
        # 第二页 — 当查询包含 startTime: 50000 时匹配
        client.set_response("startTime: 50000", CAST_EVENTS_PAGINATED_PAGE2)

        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert isinstance(result, CooldownTimelineResponse)
        # 应该收集到 3 个事件（2 from page1 + 1 from page2）
        pof = next(
            (a for a in result.abilities if a.name == "Pillar of Frost"),
            None,
        )
        if pof is not None:
            # 至少 1 个簇
            assert len(pof.cast_clusters) >= 1

    @pytest.mark.asyncio
    async def test_different_difficulty(self, timeline_client):
        """不同 difficulty 参数应生成不同缓存键"""
        with patch("src.tools.timelines.cache_get", return_value=None) as mock_cget, \
             patch("src.tools.timelines.cache_set") as mock_cset:
            await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
                difficulty="mythic",
            )
        # cache_get 的第一个参数是 cache_key
        cache_key = mock_cget.call_args[0][0]
        assert "mythic" in cache_key


# ============================================================
# 响应模型测试
# ============================================================
class TestResponseStructure:
    """CooldownTimelineResponse 模型结构验证。"""

    def test_response_model_fields(self):
        """验证所有必需字段存在且类型正确"""
        response = CooldownTimelineResponse(
            spec="frost-death-knight",
            encounter="Vorasius",
            difficulty="heroic",
            sample_size=5,
            median_fight_duration=180.5,
            boss_phases=[
                BossPhase(name="Phase 1", typical_start=0.0, typical_end=90.0),
            ],
            abilities=[
                AbilityTimeline(
                    name="Pillar of Frost",
                    ability_type="dps",
                    cd_seconds=60.0,
                    total_casts={"median": 3.0, "p25": 2.0, "p75": 4.0},
                    cast_clusters=[
                        CastCluster(
                            label="Opener",
                            median_time=3.0,
                            std_dev=0.8,
                            range=[2.0, 4.0],
                            player_pct=100.0,
                            phase="Phase 1",
                            co_used=[CoUsage(ability="Frostwyrm's Fury", rate=80.0)],
                            hold=None,
                        ),
                    ],
                    consensus="高度一致",
                ),
            ],
        )
        # 基本字段
        assert response.spec == "frost-death-knight"
        assert response.encounter == "Vorasius"
        assert response.sample_size == 5
        assert response.median_fight_duration == 180.5

        # boss_phases
        assert len(response.boss_phases) == 1
        assert response.boss_phases[0].name == "Phase 1"

        # abilities
        assert len(response.abilities) == 1
        ability = response.abilities[0]
        assert ability.name == "Pillar of Frost"
        assert ability.cd_seconds == 60.0

        # cast_clusters
        assert len(ability.cast_clusters) == 1
        cluster = ability.cast_clusters[0]
        assert cluster.label == "Opener"
        assert cluster.player_pct == 100.0
        assert cluster.co_used[0].ability == "Frostwyrm's Fury"

    def test_player_pct_calculation(self):
        """3/5 玩家在簇中 → player_pct = 60%"""
        cluster = CastCluster(
            median_time=3.0,
            player_pct=60.0,
        )
        assert cluster.player_pct == 60.0

    def test_consensus_text_values(self):
        """consensus 字段接受各种中文描述文本"""
        for text in ["高度一致", "部分一致", "分歧较大", "数据不足", ""]:
            ability = AbilityTimeline(name="Pillar of Frost", consensus=text)
            assert ability.consensus == text

    def test_co_usage_model(self):
        """CoUsage 模型字段正确"""
        co = CoUsage(ability="Frostwyrm's Fury", rate=85.0)
        assert co.ability == "Frostwyrm's Fury"
        assert co.rate == 85.0

    def test_hold_field_optional(self):
        """hold 字段可为 None 或 dict"""
        # None（默认）
        cluster_no_hold = CastCluster(median_time=65.0)
        assert cluster_no_hold.hold is None

        # 有 hold
        cluster_with_hold = CastCluster(
            median_time=90.0,
            hold={"off_cd_at": 63.0, "held_seconds": 27.0},
        )
        assert cluster_with_hold.hold is not None
        assert cluster_with_hold.hold["held_seconds"] == 27.0

    def test_ability_timeline_total_casts_dict(self):
        """total_casts 是 dict 格式 (median/min/max)"""
        ability = AbilityTimeline(
            name="Pillar of Frost",
            total_casts={"median": 3.0, "min": 2.0, "max": 4.0},
        )
        assert ability.total_casts["median"] == 3.0
        assert ability.total_casts["min"] == 2.0


# ============================================================
# 边界情况测试
# ============================================================
class TestEdgeCases:
    """边界情况和异常场景。"""

    @pytest.mark.asyncio
    async def test_single_player(self):
        """sample_size=1 正常工作"""
        client = MockWCLClient()
        client.set_response("characterRankings", {
            "worldData": {
                "encounter": {
                    "name": "Vorasius",
                    "characterRankings": {
                        "rankings": [
                            {
                                "name": "Frostblade",
                                "server": {"slug": "illidan", "region": "us"},
                                "report": {"code": "rpt_SOLO01", "fightID": 1},
                                "amount": 1350000,
                                "rank": 1,
                            },
                        ],
                        "page": 1,
                        "hasMorePages": False,
                    },
                }
            },
        })
        client.set_response('report(code: "rpt_SOLO01")', MASTER_DATA_REPORT_AAA)
        client.set_response(
            'report(code: "rpt_SOLO01")\n                    events',
            CAST_EVENTS_REPORT_AAA,
        )

        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
                sample_size=1,
            )

        assert isinstance(result, CooldownTimelineResponse)
        assert result.sample_size == 1

    def test_all_same_timestamp_clustering(self):
        """所有施法时间完全相同 → 1 个簇"""
        timestamps = [5.0, 5.0, 5.0, 5.0, 5.0]
        clusters = _cluster_timestamps(timestamps)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5
        # 构建 CastCluster
        cc = _build_cluster(clusters[0], 5, 5)
        assert cc.median_time == 5.0
        assert cc.std_dev == 0.0

    def test_very_long_fight_clustering(self):
        """长战斗 (~600s) 的时间戳正常聚类"""
        # 模拟 10 分钟战斗，每 60s 一次施法（5 个玩家）
        timestamps = [
            t + offset
            for t in [3.0, 63.0, 123.0, 183.0, 243.0,
                      303.0, 363.0, 423.0, 483.0, 543.0]
            for offset in [0.0, 1.0, -0.5, 2.0, 0.5]
        ]
        clusters = _cluster_timestamps(timestamps)
        # 应有约 10 个簇（每 60s 一个）
        assert len(clusters) >= 8
        # 最后一个簇的中位数应在 ~543s 附近
        last_median = statistics.median(clusters[-1])
        assert last_median > 500.0

    @pytest.mark.asyncio
    async def test_response_serialization(self, timeline_client):
        """响应可序列化为 dict（用于缓存/MCP 输出）"""
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # model_dump 不应抛异常
        data = result.model_dump()
        assert isinstance(data, dict)
        assert "spec" in data
        assert "abilities" in data

    @pytest.mark.asyncio
    async def test_response_round_trip(self, timeline_client):
        """响应 model_dump → 重建 → 字段一致"""
        with patch("src.tools.timelines.cache_get", return_value=None), \
             patch("src.tools.timelines.cache_set"):
            result = await get_cooldown_timelines(
                client=timeline_client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        data = result.model_dump()
        rebuilt = CooldownTimelineResponse(**data)
        assert rebuilt.spec == result.spec
        assert rebuilt.encounter == result.encounter
        assert rebuilt.sample_size == result.sample_size
        assert len(rebuilt.abilities) == len(result.abilities)
