"""The public-id minter — the ONE place a ``uid`` is generated.

A ``uid`` is the identifier a URL, an API path and a client see; the ``id`` beside it is the
internal INT/BIGINT that never leaves the database. A public id is **nanoid, length 12, over the 62
alphanumeric symbols** — nanoid's default set minus ``_`` and ``-``. Every ``uid`` column is
``text NOT NULL UNIQUE``, so a collision is a caught integrity error, never silent corruption.

They carry **no prefix**. ``doc_…``/``con_…`` was the old convention and is gone — ``con_`` meant
both ``crm.contacts`` (a person) and ``platform.contributions`` (money in), so the prefix failed at
exactly the distinction it existed to make, and nothing in the codebase resolves a uid without
already knowing its table.

``secrets.choice`` rather than a nanoid dependency: it draws through ``randbelow``, which REJECTS
out-of-range values rather than taking ``% len``, so it is unbiased at 62 symbols exactly as it
was at 64. Do not "simplify" it to a modulo draw — 256 is not a multiple of 62, and a modulo
would quietly favour the first eight symbols.

**Existing rows are never rewritten.** A uid is matched whole, a document's bytes live at an
object-storage key containing it, and ``audit.actions.path_params`` holds historic ones. Old rows
keep their old shape; a dev box gets one shape everywhere by reseeding.

**One kind is deliberately held back — the tenant uid** (:func:`tenant_uid` below, kept here so
the exception is impossible to miss). It is the only uid that lands somewhere other than a URL: it
becomes the firm's S3 bucket name. DuckLake / DAG identifiers strip the
``ten_`` prefix (:func:`tenant_model_key`) so a firm is ``tenant_<hex>``, not
``tenant_ten_<hex>``. Bucket names forbid ``_``,
``-`` and uppercase, and a bucket cannot be renamed once it holds a firm's files. Narrowing the
alphabet retired the IDENTIFIER half of this — no uid contains ``-`` now — so the bucket's ban on
uppercase is the whole of what is left.
Do not "finish the job" there. ``tests/test_public_ids.py`` pins both halves of this.
"""

from __future__ import annotations

import secrets
import string
import uuid

#: nanoid's default alphabet MINUS ``_`` and ``-``: 62 alphanumeric symbols. The two are URL-safe
#: but not safe everywhere a uid ends up — ``app/lake/holdings.py`` and ``app/platform_entities.py``
#: interpolate one into a SQL identifier behind an ``^[A-Za-z0-9_]+$`` guard that refuses ``-``, and
#: a uid that cannot be used as an identifier is a uid with a second shape. It costs half a bit.
#: Kept in step with ``public.nanoid()``'s own default by ``tests/test_public_ids.py``.
ALPHABET = string.ascii_letters + string.digits

#: The platform-wide public-id length. 12 symbols of a 62-symbol alphabet is 71.5 bits.
ID_LENGTH = 12


def new_id() -> str:
    """A fresh public id — 12 characters of :data:`ALPHABET`, no prefix."""
    return "".join(secrets.choice(ALPHABET) for _ in range(ID_LENGTH))


def tenant_uid() -> str:
    """A tenant uid — deliberately NOT :func:`new_id`, and deliberately still prefixed hex.

    This is the one uid that is not only a URL. ``tenant_storage._bucket_name`` strips the ``ten_``
    and puts the rest in the firm's S3 bucket name, where ``_``, ``-`` and uppercase are all
    illegal. Lake readers interpolate :func:`tenant_model_key` into SQL identifiers
    (``tenant_<hex>.positions``) behind an ``^[A-Za-z0-9_]+$`` guard that refuses ``-``.
    A bucket cannot be renamed once it holds a firm's files, so moving this kind is a
    migration, not an edit -- and it is not needed yet.

    It lives here rather than at its two call sites so the kind has ONE shape: provisioning minted
    16 hex characters and the dev bootstrap script minted 32, which is the same
    two-shapes-for-one-kind split that got the first attempt at this reverted.
    """
    return f"ten_{uuid.uuid4().hex[:16]}"


def tenant_model_key(uid: str) -> str:
    """The tenant identity used in DuckLake / DAG names.

    Tenant uids are ``ten_<hex>``. Prefixing ``tenant_`` onto that produced
    ``tenant_ten_<hex>``. The held-back prefix stays on the uid itself (S3, Postgres);
    model names strip it so the schema is ``tenant_<hex>``.
    """
    return uid.removeprefix("ten_")
