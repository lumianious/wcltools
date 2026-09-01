# WCLTools

A standalone CLI and agent skill for discussing Warcraft Logs raid timelines
across damage, tank, and healing roles.
The first release targets **12.1, The Venomous Abyss (zone 53)**. It handles
multiple specs through the same event model rather than a Balance-only rotation
checker. Current Mythic+ season and journal data are bundled for phase two;
Mythic+ run analysis remains disabled until the raid model is ready.

## Install

Extract the release ZIP and keep the entire `wcltools` folder together. Run
`wcltools.exe` on Windows (`wcltools` on other supported platforms). You can put
that folder on PATH using your normal operating-system setup; no aliases,
PowerShell profile, Python installation, MCP server, or hosted backend is needed.
Windows is the locally verified platform; other platforms require their own build
and validation. These are self-contained PyInstaller bundles, not static binaries.

The ZIP includes the CLI, bundled Chinese/English reference data, and exportable skill.
Check its adjacent `.sha256` file before distributing an unchanged release.

## WCL access

Create your own [WCL OAuth client](https://www.warcraftlogs.com/api/clients), then run:

```text
wcltools auth configure
wcltools auth status --json
```

This prompts for your WCL **client ID and client secret**, checks them, and stores
them in the OS credential store. It provides public-report access. Headless use
can set `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` in the environment instead; these
override the stored login. The executable does not automatically read `.env`.

For private reports, register a public/PKCE client with redirect
`http://localhost:8765/callback`, then:

```text
wcltools auth login --client-id YOUR_CLIENT_ID
```

Browser login requests your authorization. Expired user access never silently
falls back to public access. `auth logout` removes saved credentials; it does not
revoke tokens on WCL or erase cached reports. `doctor --json` shows local paths.
On Linux, an OS keyring backend is needed for saved login; environment credentials
remain available. No plaintext secret-store fallback is used.

**Users do not need a Blizzard API key.** For Chinese timelines, the same WCL
credentials fetch live labels from WCL's Chinese API. English names and spell IDs
are preserved. Bundled labels provide offline lookup and fallback; localization
failures are warnings, not silently fabricated translations.
Never distribute your credentials, `.env`, private reports, or report caches.

## Raid workflow

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

Use the report's actor ID to disambiguate duplicate player names. Default timeline
tracks are casts, buffs/debuffs, boss casts, and deaths. Add role evidence with
`--tracks healing,received,damage,taken,health`; `health` contains only health
values explicitly observed on healing or damage-taken events for the selected
player. It does not interpolate a continuous health curve. Use
`--tracks casts,resources` for resource events, and repeat `--spell ID` to filter
spells. `spells NAME --json` searches bundled labels; unknown live IDs still work.
Use `--refresh` to bypass the one-hour read cache. Files written with `--output`
are UTF-8 even on Windows.

JSON schema version 1 preserves original report-relative milliseconds,
pull-relative `offset_ms`, source/target IDs, spell IDs, event types, and raw event
fields. Narrowing the time range does not reset time zero. Results carry the
selection, completion flag, warnings, and report links. Human and standalone HTML
output are presentations of the same evidence; HTML needs no server or CDN.
Healing and damage events additionally expose amount, overheal, absorbed, and
same-event hit/max health when WCL supplies them. To study a healer's response,
align the healer's `casts,healing` timeline with a damaged player's
`taken,received,health` timeline using their pull-relative offsets.

Comparison is descriptive and aligned to pull start. It rejects incompatible known
encounter/difficulty/spec/version context and uses shared observed time. It does
not infer optimal rotations, avoidable downtime, exact aura uptime, resource
balances from deltas, missed cooldowns, or lost DPS. Missing context is reported.
Rankings are reference discovery, not automatic grading or a bulk download.

## Agent skill

Spell descriptions, talent trees, and boss-journal sections are local lookups:

```text
wcltools spells 468743 --describe --json
wcltools talents 鸟德 --search 旋荡星辰 --json
wcltools boss 3455 --search 痛饮 --json
wcltools mplus --limit 20 --json
wcltools mplus 毒牙祭坛 --json
wcltools mplus altar-of-fangs --boss 2878 --section 35022 --json
```

Broad reference searches return five compact candidates by default, with `total`
and `has_more`. Page with `--limit` (maximum 20) and `--offset`. A unique exact name
or ID returns details; choose `talents SPEC --node ID` or `boss ID --section ID`
when names are ambiguous. JSON preserves both languages; text output accepts
`--locale en-US`, `zh-CN`, or `both`. No reference command needs authentication.

The bundled reference covers all 40 current specs, the nine WCL zone 53 raid
encounters, and the eight dungeons and 28 bosses in WCL zone 55 / Blizzard
Mythic+ season 18. It preserves node, talent, spell, map, dungeon, WCL encounter,
and journal IDs as separate identities, filters hero trees by spec eligibility,
and records the Blizzard namespace and source URL. Missing text remains unknown.
Talent trees describe available choices, not a player's selected build in a
historical log. The `mplus` command is data discovery, not run coaching.

Tooltips are source text, **not validated numeric rules**: the live API can report
incorrect/scaled values, including a one-second major cooldown. Journal sections
are not resolved to a selected raid difficulty. Do not infer recharge rules,
damage, or exact boss schedules from these fields alone. Boss timings come from
WCL events. This data is queried by the CLI, not loaded wholesale into the skill.

```text
wcltools skill export --output PATH_TO_YOUR_AGENT_SKILLS_DIRECTORY
```

This writes a `wcl-raid` folder containing `SKILL.md` and refuses to overwrite an
existing folder. Point the destination at your agent's documented skill directory.
The skill uses the installed binary and teaches evidence-first raid investigation;
it has no MCP or agent-provider dependency. No agent configuration is modified.

## Development

```text
uv sync --extra dev
uv run pytest
uv run python scripts/build_release.py
```

Build artifacts and checksums go to `dist/`. Builds run on the target OS. The
focused suite covers credential isolation, event timing/pagination, selection and
comparison boundaries, Chinese identity, and CLI/file output. Live API and binary
smoke checks complement those tests; a mocked suite alone is not release evidence.

The package is deliberately small: `client.py` owns HTTP/cache/pagination,
`raid.py` owns report selection and raid queries, `output.py` owns presentation and
comparison, and `auth.py`/`catalog.py` own credentials and labels. `cli.py` connects
them. There is no plugin framework or parallel API client in the skill.

M+ can reuse the client, raw actor/event identity, report metadata, current-pool
journals, and output contract. Its future analysis should use
`ReportFight.dungeonPulls` for segmentation,
not assume top-level fights represent dungeon pulls. Current raid analysis rejects
keystone runs. Legacy M+ code and research remain recoverable from Git commit
`416432c`; they are not shipped or kept as an active second implementation.

Maintainers refresh references with `scripts/refresh_catalog.py` using their own
`BNET_CLIENT_ID` / `BNET_CLIENT_SECRET` environment variables. This is a release-time
operation, not an end-user requirement. The exporter pins the live Blizzard
namespace and keeps talent-tree and journal identity separate from spell IDs.
`--timeline exported.json` adds observed spell IDs to the next catalog refresh,
and existing labels are reused only when the exact Blizzard namespace still
matches. The current M+ WCL-to-Blizzard mapping is explicit and each name and ID
is checked against Blizzard before the previous catalog is replaced.
`wcltools catalog status` reports provenance, coverage, and unavailable counts.
Descriptions are not complete spell coverage or a patch-specific rotation guide.

See `LICENSE` and `NOTICE`. This project is not affiliated with Blizzard or WCL.
