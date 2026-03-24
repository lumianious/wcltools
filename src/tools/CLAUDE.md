# src/tools/ — WCL 工具模块

> MCP 工具实现层，每个文件对应一个或一组 MCP 工具。

[PROTOCOL]: 变更时更新此文档，然后检查父级

## 成员清单

| 文件 | 职责 | 公开接口 |
|------|------|----------|
| `_wcl_helpers.py` | 共享 WCL 基础设施（报告解析、玩家匹配、战斗查询） | `extract_report_code`, `find_actor_id_ci`, `query_fight_info_full` |
| `_analysis_comparisons.py` | 玩家 vs 基准对比分析（循环/冷却/防御/天赋/输出） | `compare_rotation`, `compare_cooldowns`, `compare_defensives`, `compare_build`, `compare_talent_usage`, `compare_cd_throughput` |
| `_analysis_metrics.py` | 分析指标计算（死亡/停工/CD窗口/Eclipse/问题归纳） | `analyze_deaths`, `analyze_downtime`, `analyze_cd_windows`, `analyze_eclipse_metrics`, `summarize_top_issues` |
| `analyze.py` | `analyze_player_log` 工具 — 玩家日志分析编排器 | `analyze_player_log` |
| `boss_timeline.py` | `get_boss_cast_timeline` 工具 — Boss 施法时间线 | `get_boss_cast_timeline` |
| `buff_timeline.py` | `get_buff_timeline` 工具 — Buff 事件时间线 | `get_buff_timeline` |
| `builds.py` | `get_top_builds` 工具 — 热门天赋构建 | `get_top_builds` |
| `cast_sequence.py` | `get_cast_sequence` 工具 — 施法序列提取 | `get_cast_sequence` |
| `coaching.py` | `get_coaching_context` 工具 — 教练上下文 | `get_coaching_context` |
| `defensives.py` | `get_defensive_patterns` 工具 — 防御技能模式 | `get_defensive_patterns` |
| `encounters.py` | `get_encounters` 工具 — 副本遭遇列表 | `get_encounters` |
| `examples.py` | `get_example_logs` 工具 — 示例日志 | `get_example_logs` |
| `resource_timeline.py` | `get_resource_timeline` 工具 — 资源变化时间线 | `get_resource_timeline` |
| `rotation.py` | `get_rotation_profile` 工具 — 循环基准画像 | `get_rotation_profile` |
| `spec_info.py` | `get_spec_info` 工具 — 专精信息 | `get_spec_info` |
| `timelines.py` | `get_cooldown_timelines` 工具 — CD 技能时间线 | `get_cooldown_timelines` |

## 模块依赖

```
server.py → 各工具模块（注册 MCP 工具）
analyze.py → _wcl_helpers, _analysis_comparisons, _analysis_metrics, rotation, builds, defensives, timelines
buff_timeline.py → _wcl_helpers, rotation
cast_sequence.py → _wcl_helpers, rotation
resource_timeline.py → _wcl_helpers, rotation
boss_timeline.py → data (bosses.json)
timelines.py → cache, data, builds
```

## 约定

- 以 `_` 前缀命名的模块为内部模块（`_wcl_helpers.py` 等），不直接注册为 MCP 工具
- 每个公开工具模块的第一个参数为 `client: WCLClient`
- WCL GraphQL 查询使用 f-string 模板，分页通过 `nextPageTimestamp` 处理
- 所有时间戳：WCL 返回毫秒，工具返回相对战斗开始的秒数
