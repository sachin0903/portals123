/**
 * The "Starts when…" panel's data door — the automations that start ONE journey.
 *
 * `TriggerPanel` is mounted on the flow page with three props and nothing else (`flowUid`,
 * `flowName`, `canManage`), so it loads and mutates through here rather than through the page's
 * loader and form actions. That is deliberate: the panel is a self-contained slice of the
 * automations surface living on someone else's page, and threading four form actions through the
 * builder's own `+page.server.ts` would couple the two for no benefit.
 *
 * The filter is applied HERE rather than asked of the API: `GET /automations` has no flow filter
 * and a firm is capped at 200 rules (`caps.maxRulesPerTenant`), so one list and a `flow_uid` match
 * is cheaper than a new endpoint and cannot drift from the rule tier's own ordering.
 *
 * Every verb re-checks the permission. `automations:write` is what arms a rule — deliberately NOT
 * `flows:write`: authoring a journey and arming the thing that starts it for real clients are
 * different grants (flow-contract.json → `$comment_permissions`).
 */
import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { AutomationRow } from '@graphene/ui/automations';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** The API's 422 carries a structured issue list; surface all of it, not just the first line. */
async function issueMessage(res: Response): Promise<string> {
	try {
		const body = await res.clone().json();
		const issues = body?.detail?.issues;
		if (Array.isArray(issues) && issues.length) return issues.join(' ');
	} catch {
		/* fall through to the plain detail */
	}
	return detail(res);
}

function requireRead(locals: App.Locals): void {
	if (!locals.user) error(401, 'Not signed in.');
	if (!can(locals.permissions, locals.role, 'automations:read')) {
		error(403, 'You do not have permission to view automations.');
	}
}

/** True when this caller may see the trigger list at all.
 *
 *  Read separately from `requireRead` because on GET a missing grant is NOT a failure. `automations:read`
 *  is a permission this programme introduced, so no role that existed before it holds one — which
 *  means every firm's existing staff would otherwise meet a red "couldn't load your triggers" box on
 *  a page that is otherwise theirs. Absence of permission is an answer ("this panel isn't yours"),
 *  and only a genuine fault is an error. The WRITE verbs still refuse outright: the endpoint stays
 *  the control, and hiding an affordance is never one. */
function mayRead(locals: App.Locals): boolean {
	return !!locals.user && can(locals.permissions, locals.role, 'automations:read');
}

function requireWrite(locals: App.Locals): void {
	requireRead(locals);
	if (!can(locals.permissions, locals.role, 'automations:write')) {
		error(403, 'You do not have permission to change automations.');
	}
}

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	const flow = url.searchParams.get('flow') ?? '';
	if (!flow) error(400, 'No journey specified.');
	if (!mayRead(locals)) return json({ automations: [], canWrite: false, permitted: false });
	const res = await fetch(`${apiUrl()}/automations`, { headers: authHeaders(locals.accessToken) });
	if (!res.ok) error(502, 'Could not load this journey’s triggers.');
	const all = (await res.json()) as AutomationRow[];
	return json({
		automations: all.filter((a) => a.flow_uid === flow),
		canWrite: can(locals.permissions, locals.role, 'automations:write'),
		permitted: true
	});
};

export const POST: RequestHandler = async ({ request, locals, fetch }) => {
	requireWrite(locals);
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/automations`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) error(res.status, await issueMessage(res));
	return json(await res.json());
};

export const PATCH: RequestHandler = async ({ request, locals, fetch }) => {
	requireWrite(locals);
	const { uid, ...patch } = await request.json();
	if (!uid) error(400, 'No automation specified.');
	const res = await fetch(`${apiUrl()}/automations/${encodeURIComponent(String(uid))}`, {
		method: 'PATCH',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(patch)
	});
	if (!res.ok) error(res.status, await issueMessage(res));
	return json(await res.json());
};

export const DELETE: RequestHandler = async ({ url, locals, fetch }) => {
	requireWrite(locals);
	const uid = url.searchParams.get('uid') ?? '';
	if (!uid) error(400, 'No automation specified.');
	const res = await fetch(`${apiUrl()}/automations/${encodeURIComponent(uid)}`, {
		method: 'DELETE',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json({ ok: true });
};
