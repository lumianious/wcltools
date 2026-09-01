# WCLTools

Warcraft Logs raid timeline CLI and agent skill for every damage, tank, and
healing specialization. 中文与 English 文档如下。

[中文](#中文) · [English](#english)

> [申请或管理 Warcraft Logs API 客户端 / Apply for or manage a Warcraft Logs API client](https://www.warcraftlogs.com/api/clients)

## 中文

WCLTools 是一个可分发的命令行工具与 Agent Skill，用于读取 Warcraft Logs
团本记录，讨论施法、光环、首领技能、伤害、治疗和玩家血量时间线。当前团本目标是
**12.1 毒蚀深渊（WCL zone 53）**。包内也已准备当前史诗钥石赛季、地下城和首领
手册数据，但史诗钥石实战分析仍留待第二阶段。

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
真实首领时间以 WCL 事件为准。未来 M+ 分析应使用 `ReportFight.dungeonPulls` 分段，
不能把顶层 fight 或战斗间隔猜测成每一波拉怪。

## English

WCLTools is a distributable command-line application and agent skill for reading
Warcraft Logs raid reports and discussing cast, aura, boss, damage, healing, and
observed player-health timelines. The current raid target is **12.1, The Venomous
Abyss (WCL zone 53)**. Current Mythic+ season, dungeon, and journal data are also
bundled, while Mythic+ run analysis remains a later phase.

### Installation

Extract the release ZIP and keep the complete `wcltools` directory together. Run
`wcltools.exe` on Windows, or build `wcltools` from source on another platform.
Add the directory to PATH through the operating system if desired. No PowerShell
alias, Python installation, MCP server, or hosted backend is required.

The release contains the CLI, bilingual reference catalog, and exportable skill.
Verify an unchanged ZIP with its adjacent `.sha256` file before distributing it.
Windows is the currently validated target platform.

### Apply for WCL API credentials

1. Open the [Warcraft Logs API client application page](https://www.warcraftlogs.com/api/clients).
2. Create an OAuth client and obtain its **client ID** and **client secret**.
3. Run:

```text
wcltools auth configure
wcltools auth status --json
```

WCLTools validates the credentials and stores them in the operating-system
credential store for public-report access. Headless environments may instead set
`WCL_CLIENT_ID` and `WCL_CLIENT_SECRET`; environment variables take precedence.
The executable does not automatically load `.env`.

For private reports, create a public/PKCE client with redirect URI
`http://localhost:8765/callback`, then run:

```text
wcltools auth login --client-id YOUR_CLIENT_ID
```

`auth logout` removes locally saved tokens without revoking the WCL grant or
deleting report caches. `doctor --json` checks local configuration. Linux needs a
working system keyring backend for saved login; environment credentials remain
available.

**End users do not need Blizzard API credentials.** Blizzard credentials are only
used by maintainers to refresh the bundled talent, spell, and journal catalog.
Never distribute a client secret, `.env`, private report, or report cache.

### Raid workflow

```text
wcltools encounters --zone 53 --json
wcltools specs balance-druid --json
wcltools report REPORT_URL --json
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --json --output pull.json
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --from 01:00 --to 02:00 --locale both
wcltools timeline REPORT_URL --fight 6 --player ACTOR_ID --locale both --format html --output pull.html
wcltools references --zone 53 --encounter 3470 --spec frost-mage --difficulty heroic --limit 5 --json
wcltools compare --left pull.json --right reference.json --json
```

Use the report actor ID when player names are ambiguous. Default tracks are
`casts,buffs,boss,deaths`. Choose role evidence deliberately:

- Damage and tank questions: `damage,taken`
- Healer actions: `casts,healing`
- A selected player's damage taken, incoming healing, and observed health:
  `taken,received,health`
- Resource events: `casts,resources`

`health` contains only hit/max-health evidence explicitly recorded by WCL on
healing or damage-taken events. It never interpolates a continuous health curve.
Healing and damage records also preserve amount, overheal, and absorbed values
when WCL supplies them. To investigate a healer response, export the healer and
relevant damaged players separately and align their pull-relative offsets.

JSON schema 1 preserves report-relative milliseconds, pull-relative `offset_ms`,
source and target actor IDs, spell IDs, event types, and raw event fields.
`--refresh` bypasses the one-hour read cache. `--output` always writes UTF-8,
including on Windows.

Comparison is descriptive. It does not invent an optimal rotation, lost DPS,
missed cooldowns, exact aura uptime, continuous resource balances, or whether a
quiet period was avoidable. Ranking rows are reference discovery, not automatic
grading.

### Local references and agent skill

These commands require neither WCL nor Blizzard credentials:

```text
wcltools spells 468743 --describe --json
wcltools talents balance-druid --search "Whirling Stars" --json
wcltools boss 3455 --search "Imbibe" --json
wcltools mplus --limit 20 --json
wcltools mplus altar-of-fangs --json
wcltools mplus altar-of-fangs --boss 2878 --section 35022 --json
```

The bundled catalog covers all 40 current specializations, the nine zone 53 raid
encounters, and the eight dungeons and 28 bosses in WCL zone 55 / Blizzard
Mythic+ season 18. WCL encounter IDs, Blizzard season/dungeon/map IDs, journal
IDs, talent-node IDs, and spell IDs remain distinct. `mplus` currently provides
season and journal discovery; it does not analyze keystone runs.

Reference searches return five compact candidates by default. Page with
`--limit` (maximum 20) and `--offset`. A unique name or ID returns detail; use
`talents SPEC --node ID` or `boss WCL_ENCOUNTER_ID --section ID` when names are
ambiguous. Text output supports `--locale en-US`, `zh-CN`, and `both`.

Export the agent skill with:

```text
wcltools skill export --output YOUR_AGENT_SKILLS_DIRECTORY
```

This creates `wcl-raid/SKILL.md` and refuses to overwrite an existing directory.
The skill calls the installed CLI. It does not change agent configuration and has
no MCP or agent-provider dependency.

### Development and catalog refresh

```text
uv sync --extra dev
uv run pytest
uv run python scripts/build_release.py
```

Maintainers run `scripts/refresh_catalog.py` with their own `BNET_CLIENT_ID` and
`BNET_CLIENT_SECRET`. The exporter pins the Blizzard namespace, validates the
current M+ WCL-to-Blizzard mapping, and retains the previous catalog if the new
data is incomplete. `wcltools catalog status` reports provenance, coverage, and
unavailable counts.

Talent and journal descriptions are source references, not validated numerical
rules, difficulty-specific mechanics, or a player's historical build. Actual boss
timing comes from WCL events. Future M+ analysis should segment
`ReportFight.dungeonPulls`; it must not guess pulls from aggregate fights or combat
gaps.

See `LICENSE` and `NOTICE`. WCLTools is not affiliated with Blizzard Entertainment
or Warcraft Logs.
