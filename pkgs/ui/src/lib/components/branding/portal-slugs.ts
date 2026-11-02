/**
 * Portal-page slug rules — the svelte-free half of the page contract. Split out of
 * ./portal-pages.ts so apps/client's SvelteKit param matcher (a server module) can import the
 * slug allowlists via `@graphene/ui/portal-slugs` without dragging in the twenty lucide icon
 * components portal-pages.ts wires into PAGE_ICONS. This module must import ONLY
 * ./block-contract.json (the shared Python/TS contract) — keep it that way.
 */
import contract from './block-contract.json';

/** Route slugs a page can never take — real portal routes (login, account, api, …). */
export const RESERVED_SLUGS: ReadonlySet<string> = new Set(contract.reservedSlugs);

/** The default pages' slugs — a custom page MAY take one of these to replace ("shadow") it. */
export const SHADOWABLE_SLUGS: ReadonlySet<string> = new Set(contract.shadowableSlugs);

/**
 * The shape of every valid page slug (what slugify() emits and app/branding.py accepts):
 * lowercase [a-z0-9-], 1–32 chars. The client app's `[slug]` param matcher tests candidate
 * routes against this before ever touching the branding payload, so junk paths 404 cheaply.
 */
export const PORTAL_SLUG_RE = /^[a-z0-9-]{1,32}$/;
