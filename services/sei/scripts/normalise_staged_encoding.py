"""Transcode already-staged SEI objects to UTF-8.

    uv run python scripts/normalise_staged_encoding.py            # report only
    uv run python scripts/normalise_staged_encoding.py --apply    # rewrite what needs it

A one-off backfill for a zone that predates `reports.to_utf8`. The pull normalises every file it
stages from now on, but a `--force` re-pull only reaches what SEI still has on SFTP — the drop is a
rolling window, so partitions older than it keep whatever bytes they were staged with, and the raw
models read `dt=*`. One non-UTF-8 partition in the glob fails the whole model, which reads as "this
firm has no data" (docs/sei.md).

Safe to run repeatedly, and safe on a clean zone: `to_utf8` returns a valid file byte-identical, so
nothing is rewritten and no fingerprint moves. It touches the STAGED zone only — the byte-exact
evidence is the `sftp/` archive, which this never opens.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sei_sync.objects import client, get, list_keys, put
from sei_sync.reports import to_utf8
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.normalise")

_PREFIX = "sei/staged/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="rewrite the objects (default: report only)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    settings = SeiSettings()
    s3 = client(settings)
    bucket = settings.core_bucket

    scanned = 0
    needed: list[str] = []
    for key in list_keys(s3, bucket, _PREFIX):
        scanned += 1
        body = get(s3, bucket, key)
        transcoded, encoding = to_utf8(body)
        if transcoded == body:
            continue
        needed.append(key)
        logger.info("%s was %s (%d bytes -> %d)", key, encoding, len(body), len(transcoded))
        if args.apply:
            put(s3, bucket, key, transcoded, content_type="text/plain; charset=utf-8")

    verb = "rewrote" if args.apply else "would rewrite"
    logger.info("scanned %d staged object(s); %s %d", scanned, verb, len(needed))
    if needed and not args.apply:
        logger.info("re-run with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
