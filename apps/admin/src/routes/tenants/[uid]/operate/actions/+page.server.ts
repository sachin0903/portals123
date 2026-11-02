import { fail, redirect } from '@sveltejs/kit';
import type { ActionCategory, ActionRow, RunDetailData, RunsPage } from '@graphene/ui';
import { apiParams, queryFromParams } from '@graphene/ui/book/listing';
import type { OrgRole } from '$lib/api';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Catalogue = {
	categories: ActionCategory[];
	actions: ActionRow[];
};

type Tab = 'runs' | 'approvals' | 'policies';

/** The filters the runs listing understands — the same list the platform's loader names. */
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

function tabFrom(value: string | null): Tab {
	return value === 'policies' || value === 'approvals' ? value : 'runs';
}

// Mirrors the platform Actions page onto a tenant: the same three tabs (Runs · Approvals · Policies)
// rendering the SAME shared components, proxied through the tenant target. No duplicate UI logic.
export const load: PageServerLoad = async ({ locals, fetch, url, parent, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:actions');
	const { tenant, writing } = await parent();
	const token = locals.accessToken;
	const tab = tabFrom(url.searchParams.get('tab'));

	if (tab === 'policies') {
		const [catalogueRes, rolesRes] = await Promise.all([
			targetJson<Catalogue>(fetch, token, tenant.uid, '/actions/catalogue'),
			targetJson<OrgRole[]>(fetch, token, tenant.uid, '/firm/roles')
		]);
		const actions = catalogueRes.ok ? catalogueRes.data.actions : [];
		const requested = url.searchParams.get('action');
		const selected = actions.some((row) => row.key === requested)
			? requested
			: (actions[0]?.key ?? null);
		return {
			tab,
			canManage: writing,
			categories: catalogueRes.ok ? catalogueRes.data.categories : [],
			actions,
			selected,
			roles: rolesRes.ok ? rolesRes.data.filter((role) => role.portal === 'platform') : [],
			loadError: !catalogueRes.ok || !rolesRes.ok
		};
	}

	// The runs workbench is server-driven: the question lives in the URL and the API answers it.
	// `?run=` is the picked row, read beside the page so the panel survives a reload. The
	// Approvals tab is the same workbench pinned to the gate, oldest case first.
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
	const [runsRes, selectedRes] = await Promise.all([
		targetJson<RunsPage>(fetch, token, tenant.uid, '/actions/runs', apiParams(query)),
		selectedUid
			? targetJson<RunDetailData>(
					fetch,
					token,
					tenant.uid,
					`/actions/runs/${encodeURIComponent(selectedUid)}`
				)
			: Promise.resolve(null)
	]);
	return {
		tab,
		canManage: writing,
		query,
		selectedUid,
		page: runsRes.ok ? runsRes.data : EMPTY_PAGE,
		selectedRun: selectedRes?.ok ? selectedRes.data : null,
		loadError: !runsRes.ok
	};
};

export const actions: Actions = {
	setPolicy: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const actionKey = String(data.get('action_key') ?? '').trim();
		if (!actionKey) return fail(400, { error: 'Choose an action.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			'/actions/policies',
			{
				method: 'PUT',
				body: JSON.stringify({
					action_key: actionKey,
					enabled: data.get('enabled') === 'true',
					approval_enabled: data.get('approval_enabled') === 'true',
					approval_role_slugs: data.getAll('approval_role_slugs').map(String),
					role_slugs: data.getAll('role_slugs').map(String),
					require_justification: data.get('require_justification') === 'true',
					manual: data.get('manual') === 'true'
				})
			}
		);
		if (!response.ok) return mutationFailure(response, 'Could not update this action.');
		return { ok: true, action_key: actionKey };
	},
	// Retry a run from where it stopped, or one node of it — a node is a run, so both post to the
	// same endpoint with the uid in hand.
	retry: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const uid = String(data.get('step_id') ?? data.get('run_uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'Choose a run.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/actions/runs/${encodeURIComponent(uid)}/retry`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not retry this run.');
		return { ok: true };
	},
	cancel: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? '').trim();
		const note = String(data.get('note') ?? '').trim() || null;
		if (!runUid) return fail(400, { error: 'Choose a run.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/actions/runs/${encodeURIComponent(runUid)}/cancel`,
			{ method: 'POST', body: JSON.stringify({ note }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not cancel this run.');
		return { ok: true };
	},
	// Approve or reject a held run from the Runs or Approvals tab. The API enforces who may decide; a
	// book review goes through /reviews so its lifecycle events fire, everything else the generic path.
	decide: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
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
		const path = isReview ? `/reviews/${runUid}/${decision}` : `/actions/runs/${runUid}/decision`;
		const body = isReview ? { note } : { decision, note };
		const response = await targetMutation(fetch, locals.accessToken, params.uid, path, {
			method: 'POST',
			body: JSON.stringify(body)
		});
		if (!response.ok) return mutationFailure(response, 'Could not record this decision.');
		return { ok: true };
	},
	// Send the approvers of a held run their notice again (one an hour, per run).
	remind: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? data.get('uid') ?? '').trim();
		if (!runUid) return fail(400, { error: 'Choose a run.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/actions/runs/${encodeURIComponent(runUid)}/remind`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not send the reminder.');
		return { ok: true };
	}
};
