import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { ActionCategory, ActionRow, RunDetailData, RunsPage } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingQuery } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders, type OrgRole } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

type Catalogue = {
	categories: ActionCategory[];
	actions: ActionRow[];
};

/** What the policies editor renders — the catalogue and the roles it offers — in one streamed
 *  object. */
export type ActionsBoard = {
	categories: ActionCategory[];
	actions: ActionRow[];
	selected: string | null;
	roles: OrgRole[];
	loadError: boolean;
};

/** The Runs tab's streamed payload: one page of the runs listing with its counts and facets. */
export type RunsBoard = { page: RunsPage; loadError: boolean };

/** The filters the runs listing understands. Named so an unrelated query param (`?run=`,
 *  `?tab=`) is neither forwarded to the API nor cleared by a filter change. Module-private: a
 *  `+page.server.ts` may export only what SvelteKit knows (load, actions, …), and a stray const
 *  is a 500 on every request in dev. `held` is the Approvals seat's own filter. */
const RUN_FILTER_KEYS = ['state', 'action', 'provider', 'requested_by', 'contact', 'held'] as const;

const EMPTY_PAGE: RunsPage = {
	items: [],
	total: 0,
	limit: 25,
	offset: 0,
	facets: {},
	stats: {
		running: 0,
		waiting: 0,
		awaiting_approval: 0,
		needs_attention: 0,
		failed_7d: 0,
		completed_24h: 0
	}
};

type Tab = 'runs' | 'approvals' | 'policies';

function tabFrom(value: string | null): Tab {
	return value === 'policies' || value === 'approvals' ? value : 'runs';
}

export const load: PageServerLoad = async ({ locals, fetch, url, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:actions');
	const allowed =
		can(locals.permissions, locals.role, 'actions:read') ||
		can(locals.permissions, locals.role, 'actions:write');
	if (!allowed) error(403, 'You do not have permission to view actions.');
	// Three tabs on one route: Runs (the workbench, the default), Approvals (the held runs) and
	// Policies (the per-firm editor). Each board is STREAMED — the heading renders at once and the
	// table/queue/editor lands behind it (`clients/+page.server.ts` is the pattern).
	// `redirect`/`error` stay above here.
	const tab = tabFrom(url.searchParams.get('tab'));
	const canManage = can(locals.permissions, locals.role, 'actions:write');
	if (tab === 'policies') {
		return {
			tab,
			canManage,
			board: loadBoard(locals.accessToken, fetch, url.searchParams.get('action'))
		};
	}
	// The runs workbench is SERVER-DRIVEN: search, filters, sort and the page live in the URL and
	// the API applies them (`transfers/+page.server.ts` is the pattern). `?run=` is the picked row,
	// whose detail streams beside the page so the panel survives a reload.
	//
	// The Approvals tab is the SAME workbench locked to the gate: the state filter is pinned to
	// awaiting_approval (a `?state=` in the URL cannot unpin it), the oldest case comes first, and
	// the listing shows the gate's own counters and columns.
	const query =
		tab === 'approvals'
			? {
					...queryFromParams(url.searchParams, RUN_FILTER_KEYS, {
						sort: 'requested',
						dir: 'asc'
					}),
					filters: {
						...queryFromParams(url.searchParams, RUN_FILTER_KEYS).filters,
						state: 'awaiting_approval'
					}
				}
			: queryFromParams(url.searchParams, RUN_FILTER_KEYS, {
					sort: 'requested',
					dir: 'desc'
				});
	const selectedUid = url.searchParams.get('run');
	return {
		tab,
		canManage,
		query,
		selectedUid,
		runs: loadRuns(locals.accessToken, fetch, query),
		selected: selectedUid ? loadRun(locals.accessToken, fetch, selectedUid) : null
	};
};

async function loadRuns(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ListingQuery
): Promise<RunsBoard> {
	const res = await fetchJson<RunsPage>(
		fetch,
		`${apiUrl()}/actions/runs?${apiParams(query)}`,
		authHeaders(accessToken)
	);
	if (!res.ok) return { page: EMPTY_PAGE, loadError: true };
	return { page: res.data, loadError: false };
}

async function loadRun(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<RunDetailData | null> {
	const res = await fetchJson<RunDetailData>(
		fetch,
		`${apiUrl()}/actions/runs/${encodeURIComponent(uid)}`,
		authHeaders(accessToken)
	);
	// A run that is gone (or never was) simply shows no panel; the listing is still right.
	return res.ok ? res.data : null;
}

async function loadBoard(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	requested: string | null
): Promise<ActionsBoard> {
	const headers = authHeaders(accessToken);
	const [catalogueRes, rolesRes] = await Promise.all([
		fetchJson<Catalogue>(fetch, `${apiUrl()}/actions/catalogue`, headers),
		fetchJson<OrgRole[]>(fetch, `${apiUrl()}/firm/roles`, headers)
	]);
	const actions = catalogueRes.ok ? catalogueRes.data.actions : [];
	const selected = actions.some((row) => row.key === requested)
		? requested
		: (actions[0]?.key ?? null);
	return {
		categories: catalogueRes.ok ? catalogueRes.data.categories : [],
		actions,
		selected,
		roles: rolesRes.ok ? rolesRes.data.filter((role) => role.portal === 'platform') : [],
		loadError: !catalogueRes.ok || !rolesRes.ok
	};
}

export const actions: Actions = {
	setPolicy: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'actions:write')) {
			return fail(403, { error: 'You cannot change action settings.' });
		}
		const data = await request.formData();
		const actionKey = String(data.get('action_key') ?? '').trim();
		if (!actionKey) return fail(400, { error: 'Choose an action.' });
		const res = await fetch(`${apiUrl()}/actions/policies`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				action_key: actionKey,
				enabled: data.get('enabled') === 'true',
				approval_enabled: data.get('approval_enabled') === 'true',
				approval_role_slugs: data.getAll('approval_role_slugs').map(String),
				role_slugs: data.getAll('role_slugs').map(String),
				require_justification: data.get('require_justification') === 'true',
				manual: data.get('manual') === 'true',
				notify_in_app: data.get('notify_in_app') !== 'false',
				notify_email: data.get('notify_email') === 'true'
			})
		});
		if (!res.ok) {
			return fail(res.status, { error: await detail(res), action_key: actionKey });
		}
		return { ok: true, action_key: actionKey };
	},
	// Retry a run from where it stopped, or one node of it: a node IS a run, so both post to the
	// same endpoint with the uid in hand (`step_id` from a node's own Retry, `run_uid` from the
	// run's). The engine revives that run and its failed descendants and ancestors.
	retry: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'actions:write')) {
			return fail(403, { error: 'You cannot retry this run.' });
		}
		const data = await request.formData();
		const uid = String(data.get('step_id') ?? data.get('run_uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'Choose a run.' });
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(uid)}/retry`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	cancel: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'actions:write')) {
			return fail(403, { error: 'You cannot cancel this run.' });
		}
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? '').trim();
		const note = String(data.get('note') ?? '').trim() || null;
		if (!runUid) return fail(400, { error: 'Choose a run.' });
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(runUid)}/cancel`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ note })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	// Approve or reject a held run. The API enforces who may decide (an approver role, never the
	// requester), so this proxies the decision rather than re-checking a permission. A book review
	// goes through the /reviews decide path (its lifecycle events + apply live there); everything
	// else through the generic action decision. A rejection needs a reason on both paths: the
	// person who raised it reads the note.
	decide: async ({ request, locals, fetch }) => {
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? '').trim();
		const decision = String(data.get('decision') ?? '').trim();
		const note = String(data.get('note') ?? '').trim() || null;
		const isReview = data.get('is_review') === 'true';
		if (!runUid || (decision !== 'approve' && decision !== 'reject')) {
			return fail(400, { error: 'Choose a decision.' });
		}
		if (decision === 'reject' && !note) {
			return fail(422, { error: 'Say why you are rejecting this.', field: 'note' });
		}
		const url = isReview
			? `${apiUrl()}/reviews/${runUid}/${decision}`
			: `${apiUrl()}/actions/runs/${runUid}/decision`;
		const body = isReview ? { note } : { decision, note };
		const res = await fetch(url, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	// Send the approvers of a held run their notice again. The API rate-limits it to one an hour
	// and refuses once the run has moved on; both come back as the sentence to show.
	remind: async ({ request, locals, fetch }) => {
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? data.get('uid') ?? '').trim();
		if (!runUid) return fail(400, { error: 'Choose a run.' });
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(runUid)}/remind`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
