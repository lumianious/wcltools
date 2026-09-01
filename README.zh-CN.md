# WCLTools

[English](README.md) | [简体中文](README.zh-CN.md)

WCLTools 是一个可分发的命令行工具与 Agent Skill，用于读取 Warcraft Logs
团本记录，讨论施法、光环、首领技能、伤害、治疗和玩家血量时间线。当前团本目标是
**12.1 毒蚀深渊（WCL zone 53）**。包内也提供当前史诗钥石赛季、地下城和首领
手册数据；`mplus` 命令仅用于本地参考查询，钥石实战时间线目前不受支持。

### 安装

解压发布 ZIP，并保持整个 `wcltools` 文件夹结构不变。Windows 运行
`wcltools.exe`；其他平台可从源码构建 `wcltools`。如有需要，请通过操作系统的正常
方式把目录加入 PATH。无需 PowerShell 别名、Python、MCP 服务或托管后端。

发布包包含 CLI、中英文参考数据和可导出的 Skill。分发未修改的发布包前，请使用
旁边的 `.sha256` 文件校验 ZIP。Windows 是当前经过本地验证的平台。

### 申请 WCL API 凭据

1. 打开 [Warcraft Logs API 客户端管理页面](https://www.warcraftlogs.com/api/clients)。
2. 创建 OAuth 客户端，取得 **client ID** 和 **client secret**。
3. 运行：

```text
wcltools auth configure
wcltools auth status --json
```

凭据会在验证后保存到操作系统凭据库，可访问公开报告。无界面环境也可设置
`WCL_CLIENT_ID` 与 `WCL_CLIENT_SECRET`；环境变量优先于凭据库。程序不会自动读取
`.env`。

如需访问私人报告，请在 WCL 创建 public/PKCE 客户端，将回调地址设为
`http://localhost:8765/callback`，然后运行：

```text
wcltools auth login --client-id YOUR_CLIENT_ID
```

`auth logout` 只删除本地保存的令牌，不会在 WCL 撤销授权或删除报告缓存。
`doctor --json` 可检查本地配置。Linux 需要可用的系统 keyring 后端才能保存登录；
环境变量方式仍然可用。

**普通用户不需要 Blizzard API 凭据。** Blizzard 凭据只供维护者在发布时更新包内
天赋、法术和地下城手册数据。不要分享 client secret、`.env`、私人报告或报告缓存。

### 团本工作流

```text
wcltools encounters --zone 53 --json
wcltools specs 鸟德 --json
wcltools report REPORT_URL --json
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --json --output pull.json
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --from 01:00 --to 02:00 --locale both
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --locale both --format html --output pull.html
wcltools references --zone 53 --encounter 3470 --spec 冰法 --difficulty heroic --limit 5 --json
wcltools compare --left pull.json --right reference.json --json
```

玩家重名时使用报告中的 actor ID。默认时间线包含 `casts,buffs,boss,deaths`。
不同职责可选择以下证据轨道：

- 输出与坦克：`damage,taken`
- 治疗者行为：`casts,healing`
- 目标玩家承伤、受到治疗与血量观测：`taken,received,health`
- 资源事件：`casts,resources`

`health` 只保留 WCL 在治疗或承伤事件中明确记录的血量与最大血量，不会插值生成
连续血量曲线。治疗和伤害事件也会在 WCL 提供时保留 amount、overheal 与 absorbed。
分析治疗反应时，可分别导出治疗者和受伤玩家的时间线，再按相对开怪时间对齐。

JSON schema 1 保留报告原始毫秒时间、相对开怪的 `offset_ms`、来源/目标 actor ID、
法术 ID、事件类型和原始事件字段。`--refresh` 可绕过一小时只读缓存；`--output`
在 Windows 上也始终写出 UTF-8。

比较结果只描述观测差异，不会凭空推断最佳循环、损失 DPS、漏交技能、完整光环覆盖、
连续资源余额或静默阶段是否失误。排名样本用于寻找参考报告，并不自动构成评分。

### 本地参考数据与 Agent Skill

以下命令无需 WCL 或 Blizzard 凭据：

```text
wcltools spells 468743 --describe --json
wcltools talents 鸟德 --search 旋荡星辰 --json
wcltools boss 3455 --search 痛饮 --json
wcltools mplus --limit 20 --json
wcltools mplus 毒牙祭坛 --json
wcltools mplus altar-of-fangs --boss 2878 --section 35022 --json
```

包内数据覆盖当前全部 40 个专精、zone 53 的 9 个团本首领，以及 WCL zone 55 /
Blizzard Mythic+ season 18 的 8 个地下城、28 个首领。WCL encounter ID、Blizzard
season/dungeon/map ID、journal ID、天赋节点 ID 和法术 ID 始终分别保存。
`mplus` 当前只提供赛季与手册查询，不分析钥石实战。

参考查询默认返回最多 5 个简要候选，可用 `--limit`（最大 20）和 `--offset` 翻页。
唯一的名称或 ID 会返回详情；重名时使用 `talents SPEC --node ID` 或
`boss WCL_ENCOUNTER_ID --section ID`。文字输出支持 `--locale en-US`、`zh-CN`
和 `both`。

导出 Agent Skill：

```text
wcltools skill export --output YOUR_AGENT_SKILLS_DIRECTORY
```

命令会创建 `wcl-raid/SKILL.md`，并拒绝覆盖已有目录。Skill 调用已安装的 CLI，
不会修改 Agent 配置，也不依赖 MCP 或特定 Agent 提供商。

### 开发与数据更新

```text
uv sync --extra dev
uv run pytest
uv run python scripts/build_release.py
```

维护者使用自己的 `BNET_CLIENT_ID` 和 `BNET_CLIENT_SECRET` 运行
`scripts/refresh_catalog.py`。更新器会固定 Blizzard namespace，校验当前 M+ 的
WCL/Blizzard 映射，并在数据不完整时保留旧 catalog。`wcltools catalog status`
可查看来源、覆盖率和缺失数量。

天赋和手册文字是来源参考，不是已经验证过的数值规则、难度专属机制或玩家历史配装。
真实首领时间以 WCL 事件为准。

详见 `LICENSE` 与 `NOTICE`。WCLTools 与 Blizzard Entertainment 及 Warcraft Logs 无隶属关系。
