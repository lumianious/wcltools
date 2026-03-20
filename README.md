# WoW Coach MCP Server

A Model Context Protocol (MCP) server that turns Claude into a World of Warcraft raid coach. It connects to the WarcraftLogs API to pull real performance data from top players and compare it against your own logs.

Built for Claude Desktop. Supports all 13 classes, 40 specs.

## What It Does

9 tools that give Claude access to WoW coaching data:

| Tool | What It Does | API Cost |
|------|-------------|----------|
| `get_coaching_context` | Session setup — preferences, rate limits, workflow tips | 0 pts |
| `get_encounters` | List current raid/dungeon bosses with IDs | ~1 pt |
| `get_spec_info` | Static spell/spec data (local, no API call) | 0 pts |
| `get_top_builds` | Meta talent builds, trinkets, stat profiles | ~2 pts |
| `get_cooldown_timelines` | When top players use major CDs during a fight | ~150 pts |
| `get_rotation_profile` | Cast counts, CPM, buff uptimes from top players | ~80 pts |
| `get_defensive_patterns` | Defensive timing, death windows, survival rate | ~60 pts |
| `get_example_logs` | Top report URLs for a spec/boss | ~1 pt |
| `analyze_player_log` | Compare YOUR log against benchmarks — gap analysis | ~5-7 pts |

All benchmark data is cached for 6 hours. Rate limit budget: 3,600 points/hour.

## Features

- **Bilingual talent/spell names** — `织星者 (Starweaver)` format. Simplified Chinese + English.
- **Talent sub-tree tagging** — Each talent tagged as `class`, `spec`, or `hero`.
- **93.6% talent coverage** — 5,548 entries mapped from WCL internal IDs to Blizzard names.
- **Smart spell resolution** — Uses WCL `masterData.abilities` at runtime for complete name coverage.
- **Personal log analysis** — Full gap analysis: rotation, cooldowns, defensives, build comparison with player's complete talent tree.
- **Coaching context** — Editable JSON config that travels with the server. Claude gets rate limit tips, workflow guidance, and user preferences automatically.

## Setup

### Prerequisites

- Python 3.10+
- [WarcraftLogs API credentials](https://www.warcraftlogs.com/api/clients) (OAuth client)
- [Blizzard API credentials](https://develop.battle.net) (for talent data export — optional, pre-built data included)
- [Claude Desktop](https://claude.ai/download)

### Install

```bash
git clone https://github.com/YourUser/wow-mcp-server.git
cd wow-mcp-server
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Configure secrets

Create a `.env` file in the project root:

```env
WCL_CLIENT_ID=your_wcl_client_id
WCL_CLIENT_SECRET=your_wcl_client_secret

# Optional — only needed if re-exporting talent data
BNET_CLIENT_ID=your_blizzard_client_id
BNET_CLIENT_SECRET=your_blizzard_client_secret
```

### Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "wow-coach": {
      "command": "/absolute/path/to/wow-mcp-server/.venv/bin/wow-mcp"
    }
  }
}
```

Restart Claude Desktop. You should see 9 tools available.

### Verify

```bash
# Run tests
.venv/bin/python -m pytest tests/ -v

# Check server imports
.venv/bin/python -c "from src.server import mcp; print('OK')"
```

## Usage

In Claude Desktop, just ask coaching questions:

- *"What talents are top Balance Druids running on Vaelgor & Ezzorak?"*
- *"Show me the cooldown timeline for Frost DK on heroic Vaelgor"*
- *"Analyze this log: https://www.warcraftlogs.com/reports/ABC123#fight=5 — I'm playing balance-druid as CharName"*
- *"Why do I keep dying in Phase 2?"*

Claude will call the appropriate tools and give you data-driven coaching.

### Coaching workflow

1. Claude calls `get_coaching_context` for session setup
2. `get_encounters` to find boss IDs
3. Benchmark tools (`get_top_builds`, `get_rotation_profile`, `get_cooldown_timelines`) for the meta
4. `analyze_player_log` with your report to get personal coaching

### Customize preferences

Edit `src/data/coaching_context.json` to change:
- Your main spec
- Current progression boss
- Language/terminology preferences
- Any other context Claude should know

## Project Structure

```
wow-mcp-server/
├── src/
│   ├── server.py              # MCP entry point, tool registration
│   ├── wcl_client.py          # WCL OAuth + GraphQL client
│   ├── cache.py               # File-based cache (~/.cache/wow-mcp/)
│   ├── models.py              # Pydantic response models
│   ├── data/
│   │   ├── __init__.py        # Data loaders (spell/talent/boss lookup)
│   │   ├── talents.json       # 5,548 talent entries (bilingual)
│   │   ├── specs.json         # 40 specs, 2,140 spells
│   │   ├── bosses.json        # 90 encounters
│   │   ├── boss_phases.json   # Static phase timing config
│   │   └── coaching_context.json
│   └── tools/
│       ├── analyze.py         # analyze_player_log
│       ├── builds.py          # get_top_builds
│       ├── coaching.py        # get_coaching_context
│       ├── defensives.py      # get_defensive_patterns
│       ├── encounters.py      # get_encounters
│       ├── examples.py        # get_example_logs
│       ├── rotation.py        # get_rotation_profile
│       ├── spec_info.py       # get_spec_info
│       └── timelines.py       # get_cooldown_timelines
├── tests/                     # 234 tests
├── scripts/
│   └── export_talent_data.py  # Blizzard + WCL talent data export
├── pyproject.toml
└── .env                       # Secrets (gitignored)
```

## Re-exporting talent data

The included `talents.json` is pre-built. To refresh after a WoW patch:

```bash
# Requires BNET_CLIENT_ID/SECRET and WCL_CLIENT_ID/SECRET in .env
.venv/bin/python scripts/export_talent_data.py
```

This fetches talent trees from Blizzard's Game Data API and builds a WCL-to-Blizzard ID bridge by sampling CombatantInfo across multiple encounters, difficulties, and ranking pages. Takes ~5-10 minutes.

## Roadmap

**Completed:** Phases 1-5 (raid coaching)

**Planned:** M+ dungeon coaching
- Phase 6: Dungeon talent builds + rotation profiles
- Phase 7: Affix-aware analysis (fortified/tyrannical splits)
- Phase 8: Pull-by-pull CD usage analysis
- Phase 9: Route integration (MDT/keystone.guru)

## License

MIT
