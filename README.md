# WCLTools

[English](README.md) | [简体中文](README.zh-CN.md)

WCLTools is a distributable command-line application and agent skill for reading
Warcraft Logs raid reports and discussing cast, aura, boss, damage, healing, and
observed player-health timelines. The current raid target is **12.1, The Venomous
Abyss (WCL zone 53)**. Current Mythic+ season, dungeon, and journal data are
bundled for local reference queries; keystone run timelines are unsupported.

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
timing comes from WCL events.

See `LICENSE` and `NOTICE`. WCLTools is not affiliated with Blizzard Entertainment
or Warcraft Logs.
