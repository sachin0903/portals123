import type { ParamMatcher } from '@sveltejs/kit';
import { PORTAL_SLUG_RE, RESERVED_SLUGS } from '@graphene/ui/portal-slugs';

/**
 * Vets `/[slug]` candidates against the portal-page contract before any branding lookup: the
 * slug must have the canonical shape (lowercase [a-z0-9-], 1–32 chars) and must not be a
 * reserved route — /login, /account, /api and friends are real portal surfaces, so as page
 * slugs they 404 here at the router. The SHADOWABLE slugs (accounts, holdings, …) pass the
 * matcher on purpose, but their static sibling routes outrank this param route, so the
 * composed page for those renders via each listing route's own shadow branch instead.
 *
 * Imports `@graphene/ui/portal-slugs` (the svelte-free half of the contract), not the root
 * barrel — a server module must not drag twenty lucide icon components into the graph.
 */
export const match: ParamMatcher = (param) =>
	PORTAL_SLUG_RE.test(param) && !RESERVED_SLUGS.has(param);
