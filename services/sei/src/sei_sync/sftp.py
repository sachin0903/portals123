"""SFTP access to the SEI drop.

SEI restricts SFTP by **source IP**. From a non-allowlisted host, auth fails with
``AuthenticationException: transport shut down or saw EOF`` — indistinguishable
from a bad password, so the error message here says so explicitly rather than
sending anyone hunting for a credential problem that isn't there. Run the pull
from a host whose egress IP SEI has allowlisted: the Coolify host, or a machine on
the company VPN, which shares that egress (verified 03 Aug 2026 — a laptop on the
VPN authenticates and lists the drop, so this is not Coolify-only).

We open the socket ourselves so the connect has a timeout: ``paramiko.Transport((host, port))``
opens one with none and hangs on the OS TCP default when the host is filtered.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sei_sync.reports import DropFile, parse
from sei_sync.settings import SeiSettings

if TYPE_CHECKING:  # paramiko is heavy; models/ import this module during discovery
    import paramiko

logger = logging.getLogger(__name__)

# paramiko logs handled connection failures as full ERROR tracebacks from its own
# transport thread; we translate them into RuntimeError, so silence the duplicate.
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class RemoteAttrs(Protocol):
    """The slice of paramiko.SFTPAttributes we read."""

    filename: str
    st_size: int | None
    st_mtime: float | None


class RemoteDir(Protocol):
    """The slice of paramiko.SFTPClient we use — lets tests pass a fake."""

    def listdir_attr(self, path: str = ".") -> list[RemoteAttrs]: ...

    def open(self, filename: str, mode: str = "r", bufsize: int = -1) -> object: ...


@contextmanager
def connect(settings: SeiSettings) -> Iterator[paramiko.SFTPClient]:
    """Yield a connected SFTP client, closing the transport on the way out."""
    import paramiko

    timeout = settings.sftp_timeout_seconds
    if not settings.sftp_username or not settings.sftp_password:
        raise RuntimeError(
            "SEI SFTP credentials missing: set SEI_SFTP_USERNAME and SEI_SFTP_PASSWORD"
        )

    transport = None
    try:
        sock = socket.create_connection((settings.sftp_host, settings.sftp_port), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.connect(username=settings.sftp_username, password=settings.sftp_password)
        client = paramiko.SFTPClient.from_transport(transport)
        if client is None:  # pragma: no cover — paramiko returns None only if the channel died
            raise RuntimeError("SFTP channel could not be opened")
        # Bound each round-trip too, not just the connect: a stalled read would
        # otherwise hang the pull indefinitely.
        channel = client.get_channel()
        if channel is not None:
            channel.settimeout(timeout)
        yield client
    except paramiko.AuthenticationException as exc:
        raise RuntimeError(
            f"SEI SFTP auth failed for {settings.sftp_username}@{settings.sftp_host}. "
            "SEI allowlists by source IP — if the credentials are known good, this host's "
            "egress IP is probably not allowlisted (run from the Coolify host, or connect "
            "to the company VPN, which shares its egress)."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"SEI SFTP unreachable at {settings.sftp_host}: {exc}") from exc
    finally:
        if transport is not None:
            transport.close()


def discover(client: RemoteDir, settings: SeiSettings) -> list[DropFile]:
    """Every ingestable file in the drop directory, oldest date first.

    Sorted by date so a partial run always leaves a contiguous history behind —
    a crash mid-pull can't leave yesterday missing but today present.

    Listed with attributes (``listdir_attr``), not just names: size and mtime come back
    in the same round trip the listing already costs, and the ``raw.files`` ledger
    records both. Statting each file separately would be 46 extra round trips a night
    for data the server has already sent.
    """
    path = settings.sftp_remote_dir or "."
    try:
        entries = client.listdir_attr(path)
    except OSError as exc:
        raise RuntimeError(f"cannot list SEI SFTP directory '{path}': {exc}") from exc

    found: list[DropFile] = []
    unknown: set[str] = set()
    for entry in entries:
        drop = parse(entry.filename)
        if drop is None:
            continue
        if not drop.known:
            unknown.add(drop.report)
        found.append(
            replace(
                drop,
                size=entry.st_size,
                # SFTP mtimes are epoch seconds in UTC. Kept tz-aware so the ledger
                # column is a real instant rather than a timestamp of nowhere.
                modified_at=(
                    datetime.fromtimestamp(entry.st_mtime, tz=UTC)
                    if entry.st_mtime is not None
                    else None
                ),
            )
        )

    if unknown:
        logger.warning(
            "SEI drop contains %d unrecognised report type(s), ingesting anyway: %s",
            len(unknown),
            ", ".join(sorted(unknown)),
        )
    found.sort(key=lambda d: (d.file_date, d.report))
    return found


def read_file(client: RemoteDir, settings: SeiSettings, drop: DropFile) -> bytes:
    """Fetch one drop file into memory.

    The whole set is ~30MB/day and the biggest single report is ~1.4MB, so
    in-memory keeps the pipeline streaming straight into object storage with no
    local spill (and nothing to clean up if the process dies mid-run).
    """
    base = settings.sftp_remote_dir.rstrip("/")
    remote = f"{base}/{drop.filename}" if base else drop.filename
    with client.open(remote, "rb") as handle:  # type: ignore[attr-defined]
        data: bytes = handle.read()
    return data
