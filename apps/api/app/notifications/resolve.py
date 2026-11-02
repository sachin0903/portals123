"""The EFFECTIVE notification rules for one event — the catalogue's built-in defaults overlaid with
a tenant's stored rules (``notifications.rules``).

For a given ``audit.actions`` row: take every (trigger, audience) the event fires, then per pair use
the tenant's stored rule if it has one (override copy/channels/flags; ``enabled=false`` suppresses),
else the audience's built-in default IF it is a built-in (non-built-in audiences only fire once a
firm authors a rule). The resolver + category + portal always come from the catalogue; only the
authored fields come from the row. The consumers iterate the result and produce rows / emails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications import catalogue, registry
from app.notifications.catalogue import Audience, Resolver, Trigger


@dataclass(frozen=True)
class EffectiveRule:
    #: The stored row's public id, or None for a catalogue default — a built-in genuinely has no
    #: row until `load_default_rules` makes one. Carried so the outbound-email ledger can name the
    #: rule that caused a send, which is what lets the event registry link a count back to it.
    uid: str | None
    trigger_key: str
    audience_key: str
    category: str
    portal: str
    resolve: Resolver
    title: str
    body: str | None
    link: str | None
    priority: str
    default_in_app: bool
    default_email: bool
    mandatory: bool
    # The email TEMPLATE this rule renders (an ``email.templates`` code). None → the general
    # ``notification`` wrapper. A stored rule that set no code falls back to the audience's default
    # (so the two document notices keep their own templates unless a firm changes them).
    email_code: str | None


def _from_default(trig: Trigger, aud: Audience) -> EffectiveRule:
    return EffectiveRule(
        uid=None,
        trigger_key=trig.key,
        audience_key=aud.key,
        category=trig.category,
        portal=catalogue.audience_portal(aud.key),
        resolve=aud.resolve,
        title=aud.default_title,
        body=aud.default_body,
        link=aud.default_link,
        priority="normal",
        default_in_app=aud.default_in_app,
        default_email=aud.default_email,
        mandatory=aud.mandatory,
        email_code=aud.email_code,
    )


def _from_row(trig: Trigger, aud: Audience, row: dict[str, Any]) -> EffectiveRule:
    return EffectiveRule(
        uid=row.get("uid"),
        trigger_key=trig.key,
        audience_key=aud.key,
        category=trig.category,
        portal=catalogue.audience_portal(aud.key),
        resolve=aud.resolve,  # the resolver is the catalogue's, never authored
        title=str(row["title"]),
        body=row["body"],
        link=row["link"],
        priority=str(row["priority"]),
        default_in_app=bool(row["default_in_app"]),
        default_email=bool(row["default_email"]),
        mandatory=bool(row["mandatory"]),
        # A stored rule with no explicit template keeps the audience's default (e.g. a firm
        # override of the document notice still renders "Document sent" unless it changed it).
        email_code=row.get("email_code") or aud.email_code,
    )


async def effective_rules(session: AsyncSession, action: dict[str, Any]) -> list[EffectiveRule]:
    pairs = catalogue.defaults_for(str(action["event_type"]))
    if not pairs:
        return []
    tenant_id = registry.book_tenant_id(action)
    trigger_keys = sorted({t.key for t, _ in pairs})
    stored = {
        (r["trigger_key"], r["audience"]): dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT uid, trigger_key, audience, title, body, link, priority, "
                    "       default_in_app, default_email, mandatory, enabled, email_code "
                    "FROM notifications.rules "
                    "WHERE tenant_id = :t AND trigger_key = ANY(:keys)"
                ),
                {"t": tenant_id, "keys": trigger_keys},
            )
        ).mappings()
    }
    out: list[EffectiveRule] = []
    for trig, aud in pairs:
        row = stored.get((trig.key, aud.key))
        if row is not None:
            if row["enabled"]:
                out.append(_from_row(trig, aud, row))
        elif aud.builtin:
            out.append(_from_default(trig, aud))
    return out
