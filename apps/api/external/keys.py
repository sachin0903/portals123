"""API-key verification cache.

The credential row lives in ``auth.api_keys`` (SHA-256 ``key_hash``, unique).
A hot key should not take a database connection on every call, and a revoke
in this process drops the entry immediately. Other processes learn within the
TTL. ``last_used_at`` is the write that would dominate, so it is offered at
most once a minute per key.

``mint_key`` is the one place a key is made: Graphene operations issue them from the admin portal
(``app/routers/partner_access.py``). The secret is shown once; only its digest and its first
characters are stored.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text


@dataclass(frozen=True)
class CachedKey:
    """One lookup result. ``found`` is false for a cached miss."""

    found: bool
    uid: str | None = None
    tenant_id: int | None = None
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None
    revoked: bool = False
    suspended: bool = False


class KeyCache:
    """In-process LRU keyed by the hex digest of the bearer secret."""

    def __init__(
        self,
        *,
        ttl: float = 60.0,
        miss_ttl: float = 5.0,
        maxsize: int = 10_000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl = ttl
        self.miss_ttl = miss_ttl
        self.maxsize = maxsize
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[str, tuple[float, CachedKey]] = OrderedDict()

    def get(self, key_hash: str) -> CachedKey | None:
        item = self._entries.get(key_hash)
        if item is None:
            return None
        stored_at, record = item
        age = self._clock() - stored_at
        limit = self.miss_ttl if not record.found else self.ttl
        if age > limit:
            self._entries.pop(key_hash, None)
            return None
        self._entries.move_to_end(key_hash)
        return record

    def put(self, key_hash: str, record: CachedKey) -> None:
        self._entries[key_hash] = (self._clock(), record)
        self._entries.move_to_end(key_hash)
        while len(self._entries) > self.maxsize:
            self._entries.popitem(last=False)

    def invalidate(self, key_hash: str) -> None:
        self._entries.pop(key_hash, None)


class LastUsedThrottle:
    """True at most once per ``interval`` for each key uid."""

    def __init__(self, *, interval: float = 60.0, clock: Callable[[], float] | None = None) -> None:
        self.interval = interval
        self._clock = clock or time.monotonic
        self._last: dict[str, float] = {}

    def should_write(self, key_uid: str) -> bool:
        now = self._clock()
        previous = self._last.get(key_uid)
        if previous is not None and now - previous < self.interval:
            return False
        self._last[key_uid] = now
        return True


LIVE_PREFIX = "live_"
#: How much of the secret is kept in ``key_prefix``, so an operator can tell keys apart.
DISPLAY_PREFIX_LENGTH = 12


@dataclass(frozen=True)
class MintedKey:
    """A new key. ``secret`` is the bearer; it is never stored."""

    secret: str
    key_prefix: str
    key_hash: str


SCOPES = (
    "clients:read",
    "clients:write",
    "checks:read",
    "checks:write",
    "links:write",
)

LOOKUP_SQL = text(
    """
    SELECT uid, tenant_id, scopes, expires_at, revoked_at
      FROM auth.api_keys
     WHERE key_hash = :key_hash
    """
)

TOUCH_SQL = text(
    """
    UPDATE auth.api_keys
       SET last_used_at = now()
     WHERE uid = :uid
       AND tenant_id = :tenant_id
       AND revoked_at IS NULL
    """
)


def hash_bearer(token: str) -> str:
    """SHA-256 hex of the bearer secret. ``auth.api_keys.key_hash`` stores this."""
    return hashlib.sha256(token.encode()).hexdigest()


def mint_key() -> MintedKey:
    """A new live key: ``live_`` and 43 URL-safe characters (256 bits)."""
    secret = LIVE_PREFIX + secrets.token_urlsafe(32)
    return MintedKey(
        secret=secret, key_prefix=secret[:DISPLAY_PREFIX_LENGTH], key_hash=hash_bearer(secret)
    )


def cached_from_row(row: Any) -> CachedKey:
    if row is None:
        return CachedKey(found=False)
    scopes = getattr(row, "scopes", None)
    if scopes is None and isinstance(row, dict):
        scopes = row.get("scopes")
    scope_tuple = tuple(str(item) for item in scopes) if scopes else ()
    uid = row["uid"] if isinstance(row, dict) else row.uid
    tenant_id = row["tenant_id"] if isinstance(row, dict) else row.tenant_id
    expires_at = row["expires_at"] if isinstance(row, dict) else row.expires_at
    revoked_at = row["revoked_at"] if isinstance(row, dict) else row.revoked_at
    return CachedKey(
        found=True,
        uid=str(uid),
        tenant_id=int(tenant_id),
        scopes=scope_tuple,
        expires_at=expires_at,
        revoked=revoked_at is not None,
    )


Lookup = Callable[[str], Awaitable[CachedKey]]
Touch = Callable[[CachedKey], Awaitable[None]]
TenantEnabled = Callable[[int], Awaitable[bool]]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class KeyVerifier:
    """Hash the bearer, serve it from the LRU, and touch ``last_used_at`` sparingly.

    A revoked row replaces any hot entry with a short-lived miss. ``invalidate``
    drops an entry in this process immediately; other processes learn when the
    TTL expires.
    """

    def __init__(
        self,
        lookup: Lookup,
        touch: Touch,
        *,
        cache: KeyCache | None = None,
        throttle: LastUsedThrottle | None = None,
        clock: Callable[[], datetime] | None = None,
        tenant_enabled: TenantEnabled | None = None,
    ) -> None:
        self.lookup = lookup
        self.touch = touch
        self.cache = cache or KeyCache()
        self.throttle = throttle or LastUsedThrottle()
        self._now = clock or (lambda: datetime.now(UTC))
        self._tenant_enabled = tenant_enabled
        self._known: OrderedDict[str, None] = OrderedDict()
        self._known_max = 10_000

    def invalidate(self, key_hash: str) -> None:
        self.cache.invalidate(key_hash)

    def cached(self, bearer: str) -> CachedKey | None:
        """The hot entry, if any. Does not open a lookup."""
        return self.cache.get(hash_bearer(bearer))

    def known(self, bearer: str) -> bool:
        """True when this process has already accepted the bearer.

        Survives the 60-second record cache so a later lookup is not treated as
        an unknown token.
        """
        return hash_bearer(bearer) in self._known

    def remember(self, bearer: str) -> None:
        digest = hash_bearer(bearer)
        self._known[digest] = None
        self._known.move_to_end(digest)
        while len(self._known) > self._known_max:
            self._known.popitem(last=False)

    async def authenticate(self, bearer: str) -> CachedKey | None:
        digest = hash_bearer(bearer)
        record = self.cache.get(digest)
        if record is None:
            record = await self.lookup(digest)
            if not record.found or record.revoked:
                self.cache.invalidate(digest)
                record = CachedKey(found=False)
            self.cache.put(digest, record)
        if not record.found or record.revoked or record.tenant_id is None or not record.uid:
            return None
        if record.expires_at is not None and _aware(record.expires_at) <= _aware(self._now()):
            return None
        tenant_id = record.tenant_id
        if (
            self._tenant_enabled is not None
            and tenant_id is not None
            and not await self._tenant_enabled(tenant_id)
        ):
            # Leave the cached key in place so a later re-enable is served on the
            # next request. Do not touch last_used_at for a suspended firm.
            return CachedKey(
                found=True,
                uid=record.uid,
                tenant_id=tenant_id,
                scopes=record.scopes,
                expires_at=record.expires_at,
                revoked=record.revoked,
                suspended=True,
            )
        if self.throttle.should_write(record.uid):
            await self.touch(record)
        return record
