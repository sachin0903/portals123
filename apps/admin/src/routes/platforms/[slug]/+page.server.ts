import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** One reason the merge refused rows, with how many it refused. */
export type RejectReason = {
	entity: string;
	reason: string;
	rejected: number;
	last_seen: string;
	tenants: number;
	tenant_name: string | null;
};

export type Rejects = { reasons: RejectReason[]; rejected: number };

/** One entity's share of a merge run: what it wrote, refused, and observed. */
export type LedgerEntity = {
	entity: string;
	created: number;
	updated: number;
	rejected: number;
	/** Pooled reason -> count. Carries the counted OBSERVATIONS that are deliberately not
	 *  rejects ("unmodelled model wrapper: SSAS"), which appear in no other table. */
	reasons: Record<string, number>;
	/** Set when THIS entity raised and the walk carried on. */
	note: string | null;
};

export type LedgerRun = {
	run_id: string;
	tenant_uid: string | null;
	tenant_name: string | null;
	run_at: string;
	created: number;
	updated: number;
	rejected: number;
	failed: number;
	entities: LedgerEntity[];
};

export type Ledger = { runs: LedgerRun[] };

/**
 * What the last merges could not write, and what they DID.
 *
 * The two halves of one question. Rejects answer what a row count cannot: a firm with no rows and a
 * firm whose rows were ALL refused look identical everywhere else. The ledger answers the one asked
 * first — did the merge run at all? — which nothing could, because `book_sink` published it only as
 * `book_{uid}.merge_ledger`, a warehouse relation holding a single run and reachable only from the
 * SQL console.
 *
 * A failure in either returns null rather than breaking the page — the rest of the overview is
 * still worth reading, and "we could not load this" must not look like "there was none".
 */
export const load: PageServerLoad = async ({ locals, params, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const provider = params.slug;
	if (!provider) return { rejects: null, ledger: null };
	const headers = authHeaders(locals.accessToken);
	const [rejectsRes, ledgerRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/platforms/${provider}/rejects?limit=25`, { headers }),
		fetch(`${apiUrl()}/admin/platforms/${provider}/ledger?limit=10`, { headers })
	]);
	return {
		rejects: rejectsRes.ok ? ((await rejectsRes.json()) as Rejects) : null,
		rejectsError: rejectsRes.ok ? null : await detail(rejectsRes),
		ledger: ledgerRes.ok ? ((await ledgerRes.json()) as Ledger) : null,
		ledgerError: ledgerRes.ok ? null : await detail(ledgerRes)
	};
};

// The Overview hosts TenantsPanel, which posts these. Form actions have to live on the route
// that submits to them, and the tenants list now lives on Overview.
/**
 * Every action here writes: enqueuing a run, dropping and rebuilding the promoted
 * environment, re-running a firm's chain. (Registering a source went with `platform.providers`
 * in 0133 — a firm's connection is configured on its own enablement now.)
 *
 * A POST runs NO layout load, so the `platforms:read` gate on `+layout.server.ts` never covered
 * them — and none of them checked anything beyond "is someone signed in". The API was, and is,
 * correctly gated (`_platforms_write`), so this was never an authorisation hole; it was a
 * READ-ONLY OPERATOR SEEING LIVE BUTTONS that answer 403 when pressed. The sibling
 * `query/run/+server.ts` already carried this exact pattern and its explanation.
 */
function refuseWithoutWrite(locals: App.Locals): ReturnType<typeof fail> | null {
	return can(locals.permissions, locals.role, 'platforms:write')
		? null
		: fail(403, { firmsError: 'You do not have permission to change platform configuration.' });
}

export const actions: Actions = {
	// Enqueue a full pipeline run on the provider's daemon (its scheduler dedupes).
	runSync: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = refuseWithoutWrite(locals);
		if (refused) return refused;
		const provider = params.slug;
		if (!provider) return fail(404, { firmsError: 'Not a live platform' });
		const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/sync`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { firmsError: await detail(res) });
		return { syncStarted: await res.json() };
	},

	// Rebuild the warehouse from source: reset Interlace-owned state, then re-run every model.
	//
	// The recovery when the daemon's state db and the DuckLake catalogue disagree — it reads a
	// model it believes it built and answers `Table with name … does not exist`. `?/runSync`
	// cannot fix that: change detection consults the same state db, so it skips exactly the
	// models that are missing. Expensive (a full re-read), so the page puts it behind a confirm.
	runRebuild: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = refuseWithoutWrite(locals);
		if (refused) return refused;
		const provider = params.slug;
		if (!provider) return fail(404, { firmsError: 'Not a live platform' });
		const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/rebuild`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { firmsError: await detail(res) });
		return { rebuildStarted: await res.json() };
	},

	// Re-run firms' per-tenant chains on the daemon — filter, stage, merge. All firms, or one
	// (hidden `tenant` input on the per-firm button).
	//
	// Two endpoints rather than one with an optional parameter, because they are addressed
	// differently: a firm is named by its numeric tenant id here and the API turns that into the uid
	// every model is tagged with. This replaced `POST /admin/fanout/run`, which is gone with the old
	// path — there is one step now, so `runSync` (the whole DAG) and this (the tenant models only)
	// are the whole menu.
	runFanout: async ({ params, locals, fetch, request }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = refuseWithoutWrite(locals);
		if (refused) return refused;
		const provider = params.slug;
		if (!provider) return fail(404, { firmsError: 'Not a live platform' });
		const data = await request.formData();
		const tenant = String(data.get('tenant') ?? '').trim();
		const path = tenant
			? `/admin/tenants/${tenant}/providers/${provider}/refresh`
			: `/admin/platforms/${provider}/refresh`;
		const res = await fetch(`${apiUrl()}${path}`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { firmsError: await detail(res) });
		return { fanout: await res.json() };
	}
};
