# tests/ — 测试套件

> 覆盖所有 MCP 工具和数据模块的单元/集成测试。

[PROTOCOL]: 变更时更新此文档，然后检查父级

## 成员清单

| 文件 | 测试目标 | 阶段 |
|------|----------|------|
| `test_encounters.py` | `get_encounters` 工具 | Phase 1 |
| `test_builds.py` | `get_top_builds` 工具 | Phase 2 |
| `test_models.py` | Pydantic 数据模型 | Phase 2 |
| `test_timelines.py` | `get_cooldown_timelines` 工具 | Phase 3 |
| `test_rotation.py` | `get_rotation_profile` 工具 | Phase 4 |
| `test_defensives.py` | `get_defensive_patterns` 工具 | Phase 4 |
| `test_examples.py` | `get_example_logs` 工具 | Phase 4 |
| `test_wcl_helpers.py` | `_wcl_helpers` 共享工具函数 | 基础设施 |
| `test_wcl_client.py` | WCL API 客户端 | 基础设施 |
| `test_cache.py` | 缓存模块 | 基础设施 |
| `test_analyze.py` | `analyze_player_log` 工具 | Phase 5 |
| `test_downtime.py` | Downtime/GCD 分析 | Phase 6A |
| `test_cd_windows.py` | CD 窗口事件关联 | Phase 6B |
| `test_talent_usage.py` | 天赋技能使用分析 | Phase 6C |
| `test_cd_throughput.py` | CD 窗口输出分析 | Phase 6D |
| `test_apl.py` | APL 循环检查 | Phase 6E |
| `test_mechanic_alignment.py` | Boss 施法时间线 | Phase 7 |
| `test_cast_sequence.py` | 施法序列提取 | Phase 7 |
| `test_buff_timeline.py` | Buff 事件时间线 | Phase 7 |
| `test_resource_timeline.py` | 资源变化时间线 | Phase 7 |
| `test_phase7.py` | Phase 7 综合集成测试 | Phase 7 |
| `test_mplus_foundation.py` | M+ 基础设施（DIFFICULTY_MAP、模型、rankings 查询） | Phase 8 |

## 测试约定

- 框架: pytest + pytest-asyncio (asyncio_mode = "auto")
- Mock: pytest-mock，WCL API 调用全部 mock
- 文件头使用 `# ====` 分隔符注释块（含 [PROTOCOL] 标记）
- 测试数据: `tests/fixtures/` 目录
- 命名: `test_{模块名}.py` 对应 `src/tools/{模块名}.py`
