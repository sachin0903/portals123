"""``sei-sync`` — the drop pull. The models are driven by ``interlace`` itself.

moon run sei:pull                       # yesterday's drop from the live SFTP
uv run sei-sync pull --since 2025-12-01 # a window
uv run sei-sync pull --local-dir ~/dev/sei/p04-production --limit 50
uv run sei-sync list --local-dir ~/dev/sei/p04-production
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from sei_sync.ingest import LocalSource, ingest, ingest_sftp, select
from sei_sync.reports import DropFile
from sei_sync.settings import SeiSettings


def _default_since() -> date:
    """Yesterday, or Friday when today is Monday — SEI writes no weekend drop."""
    today = date.today()
    return today - timedelta(days=3 if today.weekday() == 0 else 1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sei-sync", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("pull", "ingest drop files"), ("list", "show what would be ingested")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--since", type=date.fromisoformat, help="earliest file date (YYYY-MM-DD)")
        cmd.add_argument("--until", type=date.fromisoformat, help="latest file date (YYYY-MM-DD)")
        cmd.add_argument(
            "--report",
            action="append",
            dest="reports",
            help="snake_case report to include; repeatable (default: all)",
        )
        cmd.add_argument("--limit", type=int, help="stop after N files")
        cmd.add_argument(
            "--local-dir",
            type=Path,
            help="read a local directory of drop files instead of the SEI SFTP",
        )
        if name == "pull":
            cmd.add_argument(
                "--force",
                action="store_true",
                help="re-write objects that already exist",
            )
    return parser


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    settings = SeiSettings()

    since = args.since or (None if args.local_dir else _default_since())
    reports = set(args.reports) if args.reports else None

    if args.command == "list":
        drops = (
            LocalSource(args.local_dir.expanduser()).list_drops()
            if args.local_dir
            else _remote_listing(settings)
        )
        drops = select(drops, since=since, until=args.until, reports=reports)
        if args.limit:
            drops = drops[: args.limit]
        for drop in drops:
            state = "encrypted" if drop.encrypted else "plaintext"
            print(f"{drop.file_date}  {drop.table:32} {state:9} {drop.filename}")
        print(f"\n{len(drops)} file(s) selected", file=sys.stderr)
        return 0

    kwargs = {
        "since": since,
        "until": args.until,
        "reports": reports,
        "limit": args.limit,
        "force": args.force,
    }
    if args.local_dir:
        result = ingest(settings, LocalSource(args.local_dir.expanduser()), **kwargs)
    else:
        result = ingest_sftp(settings, **kwargs)

    print(result.summary, file=sys.stderr)
    for name, error in result.failed.items():
        print(f"FAILED {name}: {error}", file=sys.stderr)
    return 1 if result.failed else 0


def _remote_listing(settings: SeiSettings) -> list[DropFile]:
    """Listing the live drop needs a connection, but nothing more — open, list, close."""
    from sei_sync.ingest import SftpSource
    from sei_sync.sftp import connect

    with connect(settings) as sftp:
        return SftpSource(settings, sftp).list_drops()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
