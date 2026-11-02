import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { EmailTemplateOption, RegistryEvent } from '@graphene/ui';

/** The endpoint's own tally, shown as the page's subtitle. */
/** The endpoint's own tally. `blocked` split into `declarable` + `actor_only` when the states
 *  were separated; declaring it here typed a key that is never sent as a `number`. */
type RegistrySummary = {
	total: number;
	assignable: number;
	ready: number;
	declarable: number;
	actor_only: number;
};
import type { PageServerLoad } from './$types';

/**
 * The notification configuration — a page UNDER Event logs, because it configures what those
 * events do rather than being a surface of its own.
 *
 * `GET /firm/event-registry` returns EVERY declared event (207) with a state each: `assignable`
 * (a catalogue trigger covers it), `ready` (it names a subject but has no trigger yet) or
 * `blocked` (it records nobody to notify). The rows are passed through untouched — the component
 * reads the triggers back off the assignable ones, so the editor is the same one the admin
 * surface mounts.
 *
 * Writes still go to `/firm/notification-rules` through the sibling `rules/` proxies: this changes
 * how rules are PRESENTED, not how they are stored.
 */
export const load: PageServerLoad = async ({ locals, fetch }) => {
	// Not signed in ⇒ the login redirect, not an (empty) render of the page (the per-loader guard
	// every other platform page uses — there is no global one).
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'notifications:read')) error(403, 'Not authorised');
	const headers = authHeaders(locals.accessToken);
	const [registryRes, tmplRes] = await Promise.all([
		fetch(`${apiUrl()}/firm/event-registry`, { headers }),
		fetch(`${apiUrl()}/firm/notification-email-templates`, { headers })
	]);

	let events: RegistryEvent[] = [];
	let summary: RegistrySummary | null = null;
	if (registryRes.ok) {
		const page = (await registryRes.json()) as {
			items: RegistryEvent[];
			summary: RegistrySummary;
		};
		events = page.items;
		summary = page.summary;
	}

	return {
		events,
		summary,
		// The flat rule list the editor reads, gathered off the rows that have one.
		// DEDUPED by uid. A trigger covering several events contributes its rules to EVERY row that
		// names one of them, so a flat concatenation hands the editor the same rule twice — and the
		// listing's optimistic mirror then holds two entries with the identical key, which can
		// disagree after a toggle and post the same draft twice on save.
		rules: [
			...new Map(events.flatMap((event) => event.rules).map((rule) => [rule.uid, rule])).values()
		],
		emailTemplates: tmplRes.ok ? ((await tmplRes.json()) as EmailTemplateOption[]) : [],
		canManage: can(locals.permissions ?? [], locals.role ?? null, 'notifications:write')
	};
};
