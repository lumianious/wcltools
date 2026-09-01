"""Command line entry point for the distributable WCL evidence tool."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import __version__
from .errors import WCLError
from . import auth, catalog, output, raid


_DIFFICULTIES = {"lfr": 1, "normal": 3, "heroic": 4, "mythic": 5}
_TRACKS = {
    "casts", "buffs", "boss", "deaths", "resources",
    "healing", "received", "damage", "taken", "health",
}


def _add_result_options(parser: argparse.ArgumentParser, formats: Iterable[str] = ("text", "json")) -> None:
    parser.add_argument("--json", action="store_true", help="emit stable JSON")
    parser.add_argument("--format", choices=tuple(formats), default=None)
    parser.add_argument("--output", metavar="FILE", help="write output as UTF-8")


def _add_reference_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=5, help="results per page (1-20)")
    parser.add_argument("--offset", type=int, default=0, help="zero-based result offset")
    parser.add_argument("--locale", choices=("en-US", "zh-CN", "both"), default="both")
    _add_result_options(parser)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wcltools",
        description="Inspect Warcraft Logs raid evidence, role timelines, and bundled current-season M+ references.",
    )
    parser.add_argument("--version", action="version", version=f"wcltools {__version__}")
    parser.add_argument("--json", dest="root_json", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")

    auth = commands.add_parser("auth", help="configure and inspect WCL access")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    configure = auth_commands.add_parser("configure", help="save a WCL OAuth client ID and secret")
    _add_result_options(configure)
    login = auth_commands.add_parser("login", help="sign in through the browser")
    login.add_argument("--client-id", default=None)
    _add_result_options(login)
    status = auth_commands.add_parser("status", help="show local authentication status")
    _add_result_options(status)
    logout = auth_commands.add_parser("logout", help="remove local WCL tokens")
    _add_result_options(logout)

    doctor = commands.add_parser("doctor", help="check local setup without signing in")
    _add_result_options(doctor)

    encounters = commands.add_parser("encounters", help="list encounters for a raid zone")
    encounters.add_argument("--zone", type=int, default=53)
    encounters.add_argument("--refresh", action="store_true")
    _add_result_options(encounters)

    specs = commands.add_parser("specs", help="list or search the local spec catalog")
    specs.add_argument("query", nargs="?")
    _add_result_options(specs)

    spells = commands.add_parser("spells", help="search the local spell catalog")
    spells.add_argument("query")
    spells.add_argument("--describe", action="store_true", help="retrieve a bounded description reference")
    _add_reference_options(spells)

    talents = commands.add_parser("talents", help="search one specialization's bundled talent tree")
    talents.add_argument("spec")
    talent_filter = talents.add_mutually_exclusive_group()
    talent_filter.add_argument("--search")
    talent_filter.add_argument("--node", type=int)
    _add_reference_options(talents)

    boss = commands.add_parser("boss", help="search a WCL encounter's bundled journal sections")
    boss.add_argument("encounter", type=int, help="WCL encounter ID, not Blizzard journal ID")
    boss_filter = boss.add_mutually_exclusive_group()
    boss_filter.add_argument("--search")
    boss_filter.add_argument("--section", type=int)
    _add_reference_options(boss)

    catalog = commands.add_parser("catalog", help="inspect local catalog data")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_status = catalog_commands.add_parser("status")
    _add_result_options(catalog_status)

    report = commands.add_parser("report", help="discover a report, fights, and actors")
    report.add_argument("reference")
    report.add_argument("--refresh", action="store_true")
    _add_result_options(report)

    timeline = commands.add_parser("timeline", help="fetch a normalized player timeline")
    timeline.add_argument("reference")
    timeline.add_argument("--fight", type=int)
    timeline.add_argument("--player", required=True)
    timeline.add_argument(
        "--tracks",
        default=None,
        help="comma-separated casts,buffs,boss,deaths,resources,healing,received,damage,taken,health",
    )
    timeline.add_argument("--spell", dest="spells", action="append", default=[])
    timeline.add_argument("--from", dest="start", default=None, metavar="TIME")
    timeline.add_argument("--to", dest="end", default=None, metavar="TIME")
    timeline.add_argument("--locale", choices=("en-US", "zh-CN", "both"), default="en-US")
    timeline.add_argument("--refresh", action="store_true")
    _add_result_options(timeline, ("text", "json", "html"))

    mplus = commands.add_parser(
        "mplus",
        help="inspect bundled current-season Mythic+ dungeons and journal mechanics (run analysis deferred)",
    )
    mplus.add_argument("dungeon", nargs="?", help="dungeon name, slug, or bundled WCL/Blizzard ID")
    mplus_filter = mplus.add_mutually_exclusive_group()
    mplus_filter.add_argument("--search")
    mplus_filter.add_argument("--section", type=int)
    mplus.add_argument("--boss", type=int, help="limit a dungeon lookup to a journal boss ID")
    _add_reference_options(mplus)

    references = commands.add_parser("references", help="find compatible ranking samples")
    references.add_argument("--encounter", type=int, required=True)
    references.add_argument("--spec", required=True)
    references.add_argument("--zone", type=int, default=53)
    references.add_argument("--difficulty", choices=tuple(_DIFFICULTIES), default="heroic")
    references.add_argument("--partition", type=int)
    references.add_argument("--metric", choices=("dps", "hps"))
    references.add_argument("--limit", type=int, default=5)
    references.add_argument("--region")
    references.add_argument("--refresh", action="store_true")
    _add_result_options(references)

    compare = commands.add_parser("compare", help="compare two saved timeline JSON files")
    compare.add_argument("--left", required=True, metavar="FILE")
    compare.add_argument("--right", required=True, metavar="FILE")
    _add_result_options(compare)

    skill = commands.add_parser("skill", help="export the bundled agent skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_export = skill_commands.add_parser("export")
    skill_export.add_argument("--output", required=True, metavar="DIRECTORY")
    skill_export.add_argument("--json", action="store_true")

    return parser


def _configure_stdio() -> None:
    # Python on Windows otherwise chooses a legacy code page for redirected
    # stdout, which corrupts Chinese labels in a pipe or saved transcript.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _json_requested(args: argparse.Namespace | None, argv: Sequence[str] | None = None) -> bool:
    if args is not None and bool(
        getattr(args, "root_json", False)
        or getattr(args, "json", False)
        or getattr(args, "format", None) == "json"
    ):
        return True
    if argv:
        return "--json" in argv or "--format=json" in argv
    return False


def _parse_time(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise WCLError("time value cannot be empty", "invalid_input")
    try:
        if ":" in text:
            pieces = text.split(":")
            if len(pieces) != 2 or not all(piece.strip() for piece in pieces):
                raise ValueError
            minutes = float(pieces[0])
            seconds = float(pieces[1])
            if minutes < 0 or seconds < 0 or seconds >= 60:
                raise ValueError
            return minutes * 60 + seconds
        seconds = float(text)
        if seconds < 0:
            raise ValueError
        return seconds
    except ValueError as exc:
        raise WCLError(f"invalid time value: {value}", "invalid_input") from exc


def _parse_tracks(value: str | None) -> list[str] | None:
    if value is None:
        return None
    tracks: list[str] = []
    for item in value.split(","):
        track = item.strip().lower()
        if not track:
            continue
        if track not in _TRACKS:
            raise WCLError(f"unknown track {track!r}; choose from {', '.join(sorted(_TRACKS))}", "invalid_input")
        if track not in tracks:
            tracks.append(track)
    if not tracks:
        raise WCLError("at least one timeline track is required", "invalid_input")
    return tracks


def _resolved_spec(value: str | None) -> Any:
    if value is None:
        return None
    return catalog.resolve_spec(value)


def _resolved_spell(value: str) -> int:
    result = catalog.resolve_spell(value)
    try:
        return int(result)
    except (TypeError, ValueError) as exc:
        raise WCLError(f"spell {value!r} did not resolve to an ID", "invalid_input") from exc


def _client_call(function: Callable[..., Any], *args: Any, refresh: bool = False, **kwargs: Any) -> Any:
    with auth.make_client(refresh=refresh) as client:
        result = function(client, *args, **kwargs)
        return _attach_client_metadata(result, client)


def _attach_client_metadata(result: Any, client: Any) -> Any:
    metadata = result.setdefault("metadata", {})
    metadata["rate_limit"] = client.rate_limit
    metadata["query_count"] = client.requests + metadata.get("localization", {}).get("query_count", 0)
    return result


def _read_json(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except OSError as exc:
        raise WCLError(f"cannot read {path}: {exc}", "file_error") from exc
    except json.JSONDecodeError as exc:
        raise WCLError(f"invalid JSON in {path}: {exc.msg}", "invalid_input") from exc
    if not isinstance(value, dict):
        raise WCLError(f"timeline file {path} must contain a JSON object", "invalid_input")
    return value


def _write_text(path_value: str, value: str) -> None:
    path = Path(path_value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
    except OSError as exc:
        raise WCLError(f"cannot write {path}: {exc}", "file_error") from exc


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as exc:
        raise WCLError(f"result is not JSON serializable: {exc}", "internal_error") from exc


def _emit_result(value: Any, args: argparse.Namespace, *, default_format: str = "text") -> None:
    requested = "json" if _json_requested(args) else getattr(args, "format", None)
    format_name = requested or default_format
    if format_name == "json":
        rendered = _json_text(value)
    elif format_name == "html":
        rendered = output.render_html(value, getattr(args, "locale", "en-US"))
    else:
        rendered = output.render_text(value, getattr(args, "locale", "en-US"))
    destination = getattr(args, "output", None)
    if destination:
        _write_text(destination, rendered)
    else:
        sys.stdout.write(rendered)


def _dispatch_auth(args: argparse.Namespace) -> int:
    if args.auth_command == "configure":
        value = auth.configure()
    elif args.auth_command == "login":
        value = auth.login(client_id=args.client_id)
    elif args.auth_command == "status":
        value = auth.status()
    else:
        value = auth.logout()
    _emit_result(value, args)
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "auth":
        return _dispatch_auth(args)
    if args.command == "doctor":
        value = {
            "kind": "doctor",
            "auth": auth.status(),
            "paths": auth.paths(),
            "catalog": catalog.status(),
        }
        _emit_result(value, args)
        return 0
    if args.command == "encounters":
        value = _client_call(raid.zone, zone_id=args.zone, refresh=args.refresh)
        _emit_result(value, args)
        return 0
    if args.command == "specs":
        specs = catalog.list_specs()
        query = (args.query or "").strip().casefold()
        if query:
            def matches(spec: Mapping[str, Any]) -> bool:
                values = []
                for key in ("slug", "name", "name_en", "name_zh", "class_name", "wcl_spec"):
                    values.append(spec.get(key, ""))
                values.extend(spec.get("aliases", []) or [])
                return any(query in str(value).casefold() for value in values)
            specs = [spec for spec in specs if isinstance(spec, Mapping) and matches(spec)]
        _emit_result(specs, args)
        return 0
    if args.command == "spells":
        value = (catalog.describe_spells(args.query, limit=args.limit, offset=args.offset)
                 if args.describe else catalog.find_spells(args.query))
        _emit_result(value, args)
        return 0
    if args.command == "talents":
        value = catalog.talent_references(args.spec, search=args.search, node_id=args.node,
                                          limit=args.limit, offset=args.offset)
        _emit_result(value, args)
        return 0
    if args.command == "boss":
        value = catalog.boss_references(args.encounter, search=args.search, section_id=args.section,
                                        limit=args.limit, offset=args.offset)
        _emit_result(value, args)
        return 0
    if args.command == "mplus":
        value = catalog.mplus_references(
            args.dungeon,
            search=args.search,
            boss_id=args.boss,
            section_id=args.section,
            limit=args.limit,
            offset=args.offset,
        )
        _emit_result(value, args)
        return 0
    if args.command == "catalog":
        if args.catalog_command != "status":
            raise WCLError("unknown catalog command", "invalid_input")
        value = catalog.status()
        _emit_result(value, args)
        return 0
    if args.command == "report":
        value = _client_call(raid.report, args.reference, refresh=args.refresh)
        _emit_result(value, args)
        return 0
    if args.command == "timeline":
        spell_ids = [_resolved_spell(value) for value in args.spells] if args.spells else None
        with auth.make_client(refresh=args.refresh) as client:
            value = raid.timeline(client, args.reference, fight_id=args.fight, player=args.player,
                                  tracks=_parse_tracks(args.tracks),
                                  start=_parse_time(args.start) if args.start is not None else 0,
                                  end=_parse_time(args.end), spell_ids=spell_ids)
            if args.locale != "en-US":
                value = raid.localize(client, value)
            value = _attach_client_metadata(value, client)
        _emit_result(value, args)
        return 0
    if args.command == "references":
        spec = _resolved_spec(args.spec)
        value = _client_call(
            raid.references,
            zone_id=args.zone,
            encounter_id=args.encounter,
            spec=spec,
            difficulty=_DIFFICULTIES[args.difficulty],
            partition=args.partition,
            metric=args.metric,
            limit=args.limit,
            region=args.region,
            refresh=args.refresh,
        )
        _emit_result(value, args)
        return 0
    if args.command == "compare":
        value = output.compare(_read_json(args.left), _read_json(args.right))
        _emit_result(value, args)
        return 0
    if args.command == "skill":
        if args.skill_command != "export":
            raise WCLError("unknown skill command", "invalid_input")
        return _export_skill(args)
    raise WCLError("a command is required; use --help for usage", "invalid_input")


def _export_skill(args: argparse.Namespace) -> int:
    source = Path(__file__).resolve().parent / "skill" / "wcl-raid"
    destination = Path(args.output).expanduser() / "wcl-raid"
    if not source.is_dir():
        raise WCLError("bundled wcl-raid skill is not available in this build", "unavailable")
    if destination.exists():
        raise WCLError(f"refusing to overwrite existing skill directory: {destination}", "file_exists")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    except OSError as exc:
        raise WCLError(f"cannot export skill to {destination}: {exc}", "file_error") from exc
    result = {"kind": "skill_export", "path": str(destination)}
    if args.json:
        sys.stdout.write(_json_text(result))
    else:
        sys.stdout.write(f"Exported wcl-raid skill to {destination}\n")
    return 0


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    return str(code) if code else "internal_error"


def _emit_error(error: BaseException, json_mode: bool) -> None:
    message = str(error) or error.__class__.__name__
    if json_mode:
        sys.stdout.write(_json_text({"error": {"code": _error_code(error), "message": message}}))
    else:
        sys.stderr.write(f"error: {message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    _configure_stdio()
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    parser = _build_parser()
    try:
        args = parser.parse_args(argv_list)
        return _dispatch(args)
    except KeyboardInterrupt:
        # In particular, browser OAuth cancellation should be a quiet, normal
        # shell interruption rather than a traceback or a success message.
        return 130
    except WCLError as exc:
        _emit_error(exc, _json_requested(locals().get("args"), argv_list))
        return 2
    except (OSError, ValueError) as exc:
        _emit_error(WCLError(str(exc), "invalid_input"), _json_requested(locals().get("args"), argv_list))
        return 2
    except Exception as exc:  # keep command errors machine-readable at the boundary
        _emit_error(WCLError(str(exc), "internal_error"), _json_requested(locals().get("args"), argv_list))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
