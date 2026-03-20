# ============================================================
# get_defensive_patterns 工具测试
# 覆盖死亡聚类、防御技能使用率、集成流程
#
# 测试目标模块: src.tools.defensives (Phase 4)
#
# 查询流程:
#   1. characterRankings → 排行榜
#   2. report.fights → 战斗信息（时间范围）
#   3. report.events(Deaths) → 死亡事件
#   4. report.events(Casts, abilityID) → 防御技能施法
#
# [PROTOCOL]: 变更时更新此文档，然后检查父级
# ============================================================
from __future__ import annotations

import statistics
from unittest.mock import patch

import pytest

from tests.conftest import MockWCLClient


# ============================================================
# 辅助函数 — 死亡时间戳聚类（纯逻辑，镜像 defensives._cluster_timestamps）
# ============================================================
def _cluster_deaths(
    death_timestamps: list[float],
    gap_threshold: float = 15.0,
) -> list[list[float]]:
    """
    将死亡时间戳按间隔聚类。

    在 gap_threshold 秒内的死亡视为同一波死亡窗口。
    使用与 defensives.py 相同的均值判断方式。
    """
    if not death_timestamps:
        return []
    sorted_ts = sorted(death_timestamps)
    clusters: list[list[float]] = [[sorted_ts[0]]]
    for ts in sorted_ts[1:]:
        cluster_mean = statistics.mean(clusters[-1])
        if ts - cluster_mean > gap_threshold:
            clusters.append([ts])
        else:
            clusters[-1].append(ts)
    return clusters


def _calc_survival_rate(
    total_players: int, deaths: int
) -> float:
    """survival_rate = (total - deaths) / total * 100"""
    if total_players <= 0:
        return 0.0
    return max(0.0, (total_players - deaths) / total_players * 100.0)


def _calc_usage_rate(
    players_who_used: int, total_players: int
) -> float:
    """usage_rate = players_who_used / total_players * 100"""
    if total_players <= 0:
        return 0.0
    return players_who_used / total_players * 100.0


# ============================================================
# 单元测试 — 死亡聚类
# ============================================================
class TestDeathClustering:
    """死亡时间戳聚类测试。"""

    def test_cluster_deaths(self):
        """15 秒间隔内的死亡 → 同一个集群"""
        timestamps = [60.0, 62.0, 65.0, 68.0, 70.0]
        clusters = _cluster_deaths(timestamps, gap_threshold=15.0)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_two_death_windows(self):
        """两波死亡（间隔 > 15s）→ 两个集群"""
        timestamps = [60.0, 62.0, 65.0, 120.0, 122.0, 125.0]
        clusters = _cluster_deaths(timestamps, gap_threshold=15.0)
        assert len(clusters) == 2
        assert len(clusters[0]) == 3
        assert len(clusters[1]) == 3

    def test_no_deaths(self):
        """无死亡 → 空集群列表"""
        clusters = _cluster_deaths([], gap_threshold=15.0)
        assert clusters == []

    def test_single_death(self):
        """单次死亡 → 1 个集群，1 个时间戳"""
        clusters = _cluster_deaths([90.0])
        assert len(clusters) == 1
        assert clusters[0] == [90.0]

    def test_three_death_windows(self):
        """三波明显分离的死亡"""
        timestamps = [30.0, 32.0, 90.0, 92.0, 200.0, 201.0]
        clusters = _cluster_deaths(timestamps, gap_threshold=15.0)
        assert len(clusters) == 3

    def test_unsorted_input(self):
        """输入未排序 → 应自动排序后聚类"""
        timestamps = [70.0, 60.0, 65.0, 62.0]
        clusters = _cluster_deaths(timestamps, gap_threshold=15.0)
        assert len(clusters) == 1
        assert clusters[0] == sorted(timestamps)

    def test_death_window_stats(self):
        """死亡窗口的统计信息: 中位时间、死亡人数"""
        timestamps = [60.0, 62.0, 65.0, 68.0, 70.0]
        clusters = _cluster_deaths(timestamps, gap_threshold=15.0)
        window = clusters[0]
        median_time = statistics.median(window)
        assert median_time == 65.0
        assert len(window) == 5


# ============================================================
# 单元测试 — 实际工具的 _cluster_timestamps
# ============================================================
class TestToolClusterTimestamps:
    """测试工具中的 _cluster_timestamps 函数。"""

    def test_basic_clustering(self):
        """基本聚类: 两组远隔的时间戳"""
        try:
            from src.tools.defensives import _cluster_timestamps
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        clusters = _cluster_timestamps([2.0, 3.0, 4.0, 80.0, 82.0])
        assert len(clusters) == 2
        assert len(clusters[0]) == 3
        assert len(clusters[1]) == 2

    def test_empty_input(self):
        """空输入 → 空列表"""
        try:
            from src.tools.defensives import _cluster_timestamps
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        assert _cluster_timestamps([]) == []

    def test_single_timestamp(self):
        """单个时间戳 → 1 个簇"""
        try:
            from src.tools.defensives import _cluster_timestamps
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        clusters = _cluster_timestamps([42.0])
        assert len(clusters) == 1
        assert clusters[0] == [42.0]


# ============================================================
# 单元测试 — 生存率计算
# ============================================================
class TestSurvivalRate:
    """生存率计算测试。"""

    def test_no_deaths_100_percent(self):
        """无死亡 → 100% 生存率"""
        rate = _calc_survival_rate(20, 0)
        assert rate == 100.0

    def test_all_dead_0_percent(self):
        """全灭 → 0% 生存率"""
        rate = _calc_survival_rate(20, 20)
        assert rate == 0.0

    def test_partial_deaths(self):
        """5/20 死亡 → 75% 生存率"""
        rate = _calc_survival_rate(20, 5)
        assert rate == 75.0

    def test_zero_players(self):
        """0 玩家 → 0% 生存率（防除零）"""
        rate = _calc_survival_rate(0, 0)
        assert rate == 0.0

    def test_over_deaths_clamped(self):
        """死亡数 > 玩家数 → 0% 生存率（clamp 下限）"""
        rate = _calc_survival_rate(20, 25)
        assert rate == 0.0


# ============================================================
# 单元测试 — 防御技能使用率
# ============================================================
class TestDefensiveTimings:
    """防御技能使用率测试。"""

    def test_defensive_usage_rate(self):
        """usage_rate = players_who_used / total_players * 100"""
        rate = _calc_usage_rate(15, 20)
        assert rate == 75.0

    def test_all_players_used(self):
        """所有玩家都使用 → 100%"""
        rate = _calc_usage_rate(20, 20)
        assert rate == 100.0

    def test_no_player_used(self):
        """无人使用 → 0%"""
        rate = _calc_usage_rate(0, 20)
        assert rate == 0.0

    def test_zero_total_players(self):
        """0 玩家 → 0%（防除零）"""
        rate = _calc_usage_rate(5, 0)
        assert rate == 0.0

    def test_single_player_used(self):
        """1/20 使用 → 5%"""
        rate = _calc_usage_rate(1, 20)
        assert rate == 5.0


# ============================================================
# 数据模型测试
# ============================================================
class TestDefensiveModels:
    """防御模式响应模型验证。"""

    def test_response_model_construction(self):
        """DefensivePatternResponse 基本构造"""
        from src.tools.defensives import DefensivePatternResponse

        response = DefensivePatternResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            sample_size=10,
            fight_duration_median=300.0,
            survival_rate=90.0,
        )
        assert response.spec == "frost-death-knight"
        assert response.encounter_id == 3001
        assert response.sample_size == 10
        assert response.survival_rate == 90.0
        assert response.death_windows == []
        assert response.defensive_timings == []

    def test_empty_response(self):
        """空响应（无数据）"""
        from src.tools.defensives import DefensivePatternResponse

        response = DefensivePatternResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            difficulty="heroic",
        )
        assert response.sample_size == 0
        assert response.survival_rate == 0.0

    def test_death_window_model(self):
        """DeathWindow 模型字段"""
        from src.tools.defensives import DeathWindow

        window = DeathWindow(
            time_range="90s-95s",
            median_time=92.5,
            death_count=5,
            common_causes=["Void Blast", "Shadow Strike"],
        )
        assert window.median_time == 92.5
        assert window.death_count == 5
        assert len(window.common_causes) == 2

    def test_defensive_timing_model(self):
        """DefensiveTiming 模型字段"""
        from src.tools.defensives import DefensiveTiming

        timing = DefensiveTiming(
            name="Anti-Magic Shell",
            spell_id=48707,
            clusters=[
                {"median_time": 5.0, "player_pct": 80.0},
                {"median_time": 90.0, "player_pct": 60.0},
            ],
            usage_rate=85.0,
        )
        assert timing.name == "Anti-Magic Shell"
        assert timing.spell_id == 48707
        assert len(timing.clusters) == 2
        assert timing.usage_rate == 85.0

    def test_serialization_round_trip(self):
        """响应 model_dump → 重建 → 字段一致"""
        from src.tools.defensives import (
            DefensivePatternResponse,
            DefensiveTiming,
            DeathWindow,
        )

        original = DefensivePatternResponse(
            spec="frost-death-knight",
            encounter_id=3001,
            encounter_name="Vorasius",
            difficulty="heroic",
            sample_size=10,
            fight_duration_median=300.0,
            defensive_timings=[
                DefensiveTiming(
                    name="Anti-Magic Shell",
                    spell_id=48707,
                    usage_rate=85.0,
                ),
            ],
            death_windows=[
                DeathWindow(
                    time_range="90s-95s",
                    median_time=92.5,
                    death_count=5,
                ),
            ],
            survival_rate=90.0,
        )
        data = original.model_dump()
        rebuilt = DefensivePatternResponse(**data)
        assert rebuilt.spec == original.spec
        assert rebuilt.sample_size == original.sample_size
        assert len(rebuilt.defensive_timings) == 1
        assert len(rebuilt.death_windows) == 1
        assert rebuilt.survival_rate == original.survival_rate


# ============================================================
# 单元测试 — 聚合函数
# ============================================================
class TestAggregationFunctions:
    """测试工具中的聚合函数。"""

    def test_aggregate_death_windows_empty(self):
        """无死亡 → 空窗口列表"""
        try:
            from src.tools.defensives import _aggregate_death_windows
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        result = _aggregate_death_windows([])
        assert result == []

    def test_aggregate_death_windows_basic(self):
        """基本死亡窗口聚合"""
        try:
            from src.tools.defensives import _aggregate_death_windows
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        deaths = [
            {"relative_sec": 90.0, "killing_ability": "Void Blast"},
            {"relative_sec": 91.0, "killing_ability": "Void Blast"},
            {"relative_sec": 92.0, "killing_ability": "Shadow Strike"},
        ]
        windows = _aggregate_death_windows(deaths)
        assert len(windows) >= 1
        # 所有死亡在同一时间窗口
        total_deaths = sum(w.death_count for w in windows)
        assert total_deaths == 3

    def test_aggregate_defensive_timings_no_casts(self):
        """无施法记录 → usage_rate = 0"""
        try:
            from src.tools.defensives import _aggregate_defensive_timings
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        defensive_spells = {48707: "Anti-Magic Shell"}
        spell_casts: dict[int, list[float]] = {}
        result = _aggregate_defensive_timings(
            defensive_spells, spell_casts, total_fights=5
        )
        assert len(result) == 1
        assert result[0].usage_rate == 0.0

    def test_aggregate_defensive_timings_with_casts(self):
        """有施法记录 → usage_rate > 0, 有聚类"""
        try:
            from src.tools.defensives import _aggregate_defensive_timings
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        defensive_spells = {48707: "Anti-Magic Shell"}
        spell_casts = {48707: [5.0, 6.0, 7.0, 90.0, 91.0]}
        result = _aggregate_defensive_timings(
            defensive_spells, spell_casts, total_fights=5
        )
        assert len(result) == 1
        timing = result[0]
        assert timing.usage_rate > 0
        # 应有 2 个聚类（~5s 和 ~90s）
        assert len(timing.clusters) == 2


# ============================================================
# 复合场景测试 — 死亡窗口 + 防御使用
# ============================================================
class TestDeathWindowDefensiveCorrelation:
    """死亡窗口与防御技能使用的关联分析测试。"""

    def test_high_death_low_defensive(self):
        """死亡窗口中防御使用率低 → 应被标记为优化建议"""
        death_ts = [90.0, 91.0, 92.0, 93.0, 94.0]
        clusters = _cluster_deaths(death_ts)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

        usage_rate = _calc_usage_rate(3, 20)
        assert usage_rate == 15.0

        survival = _calc_survival_rate(20, 5)
        assert survival == 75.0

    def test_no_death_window_high_defensive(self):
        """无死亡窗口 + 高防御使用率 → 团队表现好"""
        clusters = _cluster_deaths([])
        assert len(clusters) == 0

        usage_rate = _calc_usage_rate(18, 20)
        assert usage_rate == 90.0

        survival = _calc_survival_rate(20, 0)
        assert survival == 100.0


# ============================================================
# 集成测试 — 完整流程（mock WCL）
#
# 注意: get_defensive_patterns 依赖 _get_defensive_spells(spec)
# 从 specs.json 读取防御技能标签。如果 spec 没有 defensive 标签
# 的技能，工具会提前返回空结果。
# 因此集成测试需要 mock _get_defensive_spells 或使用有标签的 spec。
# ============================================================

DEFENSIVE_RANKINGS_RESPONSE = {
    "worldData": {
        "encounter": {
            "name": "Vorasius",
            "characterRankings": {
                "rankings": [
                    {
                        "name": f"Player{i}",
                        "server": {"slug": "illidan", "region": "us"},
                        "report": {"code": f"rpt_DEF{i:03d}", "fightID": 1},
                        "amount": 800_000 + i * 5_000,
                        "rank": i + 1,
                        "duration": 300_000,  # 300 秒（毫秒）
                    }
                    for i in range(5)
                ],
                "page": 1,
                "hasMorePages": False,
            },
        }
    },
}

DEFENSIVE_FIGHT_INFO = {
    "reportData": {
        "report": {
            "fights": [
                {
                    "startTime": 100_000,
                    "endTime": 400_000,
                    "kill": True,
                }
            ]
        }
    },
}

DEFENSIVE_DEATH_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {
                        "type": "death",
                        "timestamp": 190_000,
                        "targetID": 10,
                        "killingAbility": {"name": "Void Blast", "guid": 400001},
                    },
                    {
                        "type": "death",
                        "timestamp": 191_000,
                        "targetID": 11,
                        "killingAbility": {"name": "Void Blast", "guid": 400001},
                    },
                    {
                        "type": "death",
                        "timestamp": 192_000,
                        "targetID": 12,
                        "killingAbility": {"name": "Shadow Strike", "guid": 400002},
                    },
                ],
            }
        }
    },
}

DEFENSIVE_CAST_EVENTS = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "abilityGameID": 48707,
                     "timestamp": 185_000, "sourceID": 1},
                    {"type": "cast", "abilityGameID": 48707,
                     "timestamp": 186_000, "sourceID": 2},
                ],
            }
        }
    },
}


def _setup_defensive_client() -> MockWCLClient:
    """
    为 defensives 集成测试预配置 mock WCL client。

    查询区分策略（利用最长匹配）:
    - "characterRankings" → 排行榜
    - "fights(fightIDs:" → 战斗信息
    - "dataType: Deaths" → 死亡事件
    - "abilityID:" → 防御技能施法（包含 abilityID 的 Casts 查询）
    """
    client = MockWCLClient()
    client.set_response("characterRankings", DEFENSIVE_RANKINGS_RESPONSE)
    client.set_response("fights(fightIDs:", DEFENSIVE_FIGHT_INFO)
    client.set_response("dataType: Deaths", DEFENSIVE_DEATH_EVENTS)
    client.set_response("abilityID:", DEFENSIVE_CAST_EVENTS)
    return client


class TestDefensivePatternIntegration:
    """完整流程集成测试（mock WCL）。"""

    @pytest.mark.asyncio
    async def test_basic_pattern(self):
        """应返回有效的 DefensivePatternResponse"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        # mock _get_defensive_spells 以避免依赖 specs.json 的 defensive 标签
        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        from src.tools.defensives import DefensivePatternResponse
        assert isinstance(result, DefensivePatternResponse)
        assert result.spec == "frost-death-knight"
        assert result.encounter_id == 3001
        assert result.sample_size >= 1
        assert result.encounter_name == "Vorasius"

    @pytest.mark.asyncio
    async def test_has_death_windows(self):
        """应返回死亡窗口数据"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # mock 数据包含 3 次死亡（集中在 ~90s）
        assert len(result.death_windows) >= 1
        total_deaths = sum(w.death_count for w in result.death_windows)
        assert total_deaths >= 1

    @pytest.mark.asyncio
    async def test_has_defensive_timings(self):
        """应返回防御技能时机数据"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        assert len(result.defensive_timings) >= 1
        ams = next(
            (t for t in result.defensive_timings if t.name == "Anti-Magic Shell"),
            None,
        )
        assert ams is not None
        assert ams.usage_rate > 0

    @pytest.mark.asyncio
    async def test_survival_rate(self):
        """击杀战斗应有正的存活率"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # mock 战斗都是 kill=True，存活率应 > 0
        assert result.survival_rate > 0

    @pytest.mark.asyncio
    async def test_empty_rankings(self):
        """无排名数据 → 优雅返回空结果"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = MockWCLClient()
        client.set_response("characterRankings", {
            "worldData": {
                "encounter": {
                    "name": "Vorasius",
                    "characterRankings": {
                        "rankings": [],
                        "page": 1,
                        "hasMorePages": False,
                    },
                }
            },
        })

        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        from src.tools.defensives import DefensivePatternResponse
        assert isinstance(result, DefensivePatternResponse)
        assert result.sample_size == 0

    @pytest.mark.asyncio
    async def test_no_defensive_spells(self):
        """无防御技能标签 → 返回空防御时机"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set"), \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={}):
            result = await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        # 无防御技能 → 提前返回空结果
        assert result.defensive_timings == []

    @pytest.mark.asyncio
    async def test_cached_second_call(self):
        """缓存命中时不发起额外 WCL 查询"""
        try:
            from src.tools.defensives import get_defensive_patterns
        except ImportError:
            pytest.skip("src.tools.defensives 尚未实现")

        client = _setup_defensive_client()

        # 第一次调用
        with patch("src.tools.defensives.cache_get", return_value=None), \
             patch("src.tools.defensives.cache_set") as mock_cset, \
             patch("src.tools.defensives._get_defensive_spells",
                   return_value={48707: "Anti-Magic Shell"}):
            await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )
            cached_data = mock_cset.call_args[0][1]

        count1 = client.query_call_count

        # 第二次调用（缓存命中）
        with patch("src.tools.defensives.cache_get", return_value=cached_data), \
             patch("src.tools.defensives.cache_set"):
            await get_defensive_patterns(
                client=client,
                spec="frost-death-knight",
                encounter_id=3001,
            )

        count2 = client.query_call_count
        assert count2 == count1, "缓存命中时不应有额外 WCL 查询"
