---
name: wcl-raid
description: Inspect Warcraft Logs raid reports and discuss damage, tank, and healing evidence across all specs using the wcltools CLI. Use for raid log questions and pull/player comparisons; current Mythic+ reference data is available, but run analysis is not implemented yet.
---

# Raid timeline analysis

Use the installed `wcltools` executable. It requires the user's WCL credentials,
not Blizzard API credentials or an MCP server. If the executable is missing, ask
Windows first tries `%LOCALAPPDATA%\Programs\WCLTools\wcltools.exe`, the standard
per-user installation, in case PATH has not refreshed. Otherwise ask the user to
install the WCLTools release; do not invent a shell alias or silently install a
different tool. `wcltools doctor --json` reports local setup without
logging in. Never ask the user to paste a client secret into chat.

## Find the evidence

- Inspect `wcltools report REPORT --json` to select the actual fight and actor.
  Accept URL fight selections. Resolve duplicate names with the reported actor ID.
- Fetch a player timeline with `wcltools timeline REPORT --fight ID --player ACTOR
  --json --output timeline.json`. Add `--locale both` for live WCL Chinese labels
  alongside English names, using the same credentials. Default tracks include casts, buffs, boss events,
  and deaths; add `--tracks casts,resources` for resource evidence. Narrow a large
  response with `--from 01:00 --to 02:00`; offsets still refer to pull start.
- Choose role evidence deliberately: `damage,taken` for damage and tank questions;
  `casts,healing` for a healer's actions; and `taken,received,health` for the
  selected player's damage, incoming healing, and observed health. To study a
  healer's response to raid damage, fetch the healer and relevant damaged players
  separately, then align their pull-relative offsets. Preserve amount, overheal,
  and absorbs. Health points appear only when WCL recorded them on that event;
  never interpolate a continuous party-health curve.
- Use `wcltools specs 鸟德 --json` or `wcltools spells 超凡之盟 --json` to resolve
  names. An ambiguous name needs a candidate ID, not a guess.
- For another player's example, use `wcltools references --zone 53 --encounter ID
  --spec SPEC --difficulty heroic --limit 5 --json`. Zone 53 / partition 1 is the
  initial 12.1 target. Query `encounters --zone ZONE --json` for another zone rather
  than treating these numbers as permanently current. Reference output records
  the selected partition and metric. Fetch only the samples relevant to the question.
- Compare saved timelines using `wcltools compare --left left.json --right right.json
  --json`. To show a timeline, generate `timeline ... --locale both --format html
  --output timeline.html`. This is a local artifact; do not publish it implicitly.

## Retrieve mechanics only when relevant

Use the CLI's bundled references rather than reading catalog files or loading all
talents into context. These commands work offline and require no Blizzard key:

```text
wcltools spells 468743 --describe --json
wcltools talents 鸟德 --search 旋荡星辰 --json
wcltools boss 3455 --search 痛饮 --json
wcltools mplus --limit 20 --json
wcltools mplus 毒牙祭坛 --json
```

Broad queries return candidates; a unique exact match returns details. Use
`talents SPEC --node ID` or `boss WCL_ENCOUNTER_ID --section ID` to disambiguate.
Page only when useful with `--limit` and `--offset`; inspect `total`, `has_more`,
source namespace, and warnings. Boss IDs are WCL IDs, not Blizzard journal IDs.
Tree nodes, talent IDs, and spell IDs are also distinct. Do not invent links
between spells from prose or treat available talents as the player's chosen build.

The `mplus` command bundles the current season, its eight dungeons, and their
boss journals. Select a dungeon before using `--search`, `--boss`, or `--section`.
WCL zone/encounter IDs, Blizzard season/dungeon/map IDs, and journal IDs remain
separate. This prepares phase two reference data; do not analyze a keystone run
or treat a dungeon journal as pull segmentation.

Tooltips can contain wrong/scaled numbers (including a one-second major cooldown).
Preserve this uncertainty; they are not validated recharge or damage rules.
Journal sections are not difficulty-resolved. Use WCL for actual boss timings and
the fight's recorded build where available; research current sources when reference
data is missing, contradictory, from another build, or insufficient for strategy.

## Explain only what the data supports

Check `complete`, `selection`, and `warnings` before drawing conclusions. Keep
report/fight links and pull-relative times in the answer. Describe observed events
first, then explain inferences and suggestions in the user's language.

Use actual boss casts and recorded phases. Do not invent an encounter schedule,
equate a quiet period with avoidable downtime, or grade every spec using another
spec's rules. Resource deltas are not resource balances; missing measurements remain
unknown. Aura event lists do not prove exact uptime or pre-pull state.

Compare compatible encounter, difficulty, spec, partition/patch, duration, and
kill/wipe context. Reference rankings are examples, not proof of optimal play.
Differences in build, assignments, external buffs, or strategy may explain timing.
Ordinal cast differences are descriptive, not calculated lost DPS. Unknown
cooldown resets/charges prevent exact missed-use claims. The bundled references
are source descriptions, not a rotation guide.

Do not infer that JSON emptiness means zero activity after a failed request. Stop
and report API/permission/pagination failures. A missing capability is not an
invitation to fabricate metrics. M+ needs dungeon-pull segmentation in a later
release; report discovery alone is not M+ coaching support.
