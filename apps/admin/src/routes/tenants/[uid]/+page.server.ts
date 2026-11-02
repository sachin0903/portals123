import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type Environment, type Member } from '$lib/api';
import { showsOutboundEmailGuard } from '$lib/instance-stage';
import { can } from '$lib/permissions';
import type { SectionState, TenantOverview } from '$lib/tenants/types';
import type { Actions, PageServerLoad } from './$types';

export type TenantStorage = {
	tenant_id: number;
	kind: string;
	endpoint: string;
	bucket: string;
	public_base_url: string | null;
	access_key_id: string | null;
	status: 'provisioning' | 'active' | 'suspended';
} | null;

/**
 * The firm's AI entitlement (`flows.ai_tenants`) plus its month-to-date spend.
 *
 * `configured` is the DEPLOYMENT half of the two switches — whether this deployment has an API key
 * at all. Surfaced beside the firm half because "enabled" on a deployment with no key is a setting
 * that does nothing, and an operator should be able to see that without reading the deploy config.
 */
export type TenantAI = {
	enabled: boolean;
	monthly_token_cap: number;
	tokens_this_month: number;
	calls_this_month: number;
	configured: boolean;
} | null;

export type TenantDataJob = {
	id: number;
	kind: 'reset' | 'reset_and_seed' | 'seed_book';
	status: 'queued' | 'processing' | 'completed' | 'failed';
	phase: string;
	result: Record<string, unknown>;
	error: string | null;
	providers_paused: boolean;
	created_at: string;
	completed_at: string | null;
} | null;

export type { SectionState, TenantOverview };

/** Who this environment may email. `using_default` means nothing has been saved yet. */
export type OutboundEmailAllowlist = {
	entries: string[];
	using_default: boolean;
	default_entries: string[];
};

export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { appStage } = await parent();
	// The firm's uid — what `/admin/tenants/{tenant_uid}` takes. It is the route's own parameter,
	// so nothing is resolved to get it.
	const tenantUid = params.uid;
	const headers = authHeaders(locals.accessToken);
	const guarded = showsOutboundEmailGuard(appStage);
	const [membersRes, envRes, storageRes, overviewRes, aiRes, dataJobRes, allowlistRes] =
		await Promise.all([
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members`, { headers }),
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/environment`, { headers }),
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/storage`, { headers }),
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/overview`, { headers }),
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/ai`, { headers }),
			fetch(`${apiUrl()}/admin/tenants/${tenantUid}/data-job`, { headers }),
			guarded ? fetch(`${apiUrl()}/admin/outbound-email`, { headers }) : Promise.resolve(null)
		]);
	const members: Member[] = membersRes.ok ? await membersRes.json() : [];
	// One WorkOS configuration per firm — or null when not yet configured.
	const environment: Environment | null = envRes.ok ? await envRes.json() : null;
	// The storage registry row — null when never provisioned (or the API predates it).
	const storage: TenantStorage = storageRes.ok ? await storageRes.json() : null;
	// Null when an older API 403s or does not have the route: the card then says the deployment
	// does not offer AI actions, which is the truthful reading of "this API cannot tell me".
	const ai: TenantAI = aiRes.ok ? await aiRes.json() : null;
	// Readiness per feature area. Null (not an empty object) when the API is older than the
	// endpoint, so the page can omit the panel rather than draw every light grey.
	const overview: TenantOverview | null = overviewRes.ok ? await overviewRes.json() : null;
	const dataJob: TenantDataJob = dataJobRes.ok ? await dataJobRes.json() : null;
	const emailAllowlist: OutboundEmailAllowlist | null = allowlistRes?.ok
		? await allowlistRes.json()
		: null;
	return {
		members,
		environment,
		storage,
		overview,
		ai,
		dataJob,
		dataToolsAvailable: dataJobRes.status !== 404,
		emailAllowlist
	};
};

export const actions: Actions = {
	// Provision (or retry/repair) the firm's storage: bucket, branding prefix, service
	// user and datalake catalogue — idempotent, so safe to run repeatedly.
	provisionStorage: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		// Provisioning mints buckets/keys/catalogues — a write. The page renders for tenants:read,
		// so the action refuses read-only operators itself (the API gates it too); the overview page
		// hides the button off the layout's canManage for the same reason.
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, { error: 'You need the tenants:write permission to provision storage.' });
		const tenantUid = params.uid;
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/storage/provision`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { provisioned: await res.json() };
	},

	// TEMPORARY (Sept 2026). Re-run the built-in onboarding seed over this firm, bringing it to
	// the definition this build ships. A firm is seeded once, at creation, so one created before
	// a seed change is frozen at the definition that shipped that day. Delete this action with
	// the button and the API route.
	//
	// Safe for journeys in flight: the seed is expand-only and a running journey is pinned to the
	// version it started on. NOT safe for firm-authored edits to the six seeded forms, which it
	// replaces -- which is why the API refuses this outside a development stage.
	reseedOnboarding: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, {
				error: 'You need the tenants:write permission to reseed the onboarding flow.'
			});
		const tenantUid = params.uid;
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/flows/onboarding/reseed`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { reseeded: await res.json() };
	},

	resetData: async ({ params, locals, request, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, { dataError: 'You need the tenants:write permission to reset data.' });
		const form = await request.formData();
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/data-job`, {
			method: 'POST',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify({
				kind:
					form.get('kind') === 'reset_and_seed'
						? 'reset_and_seed'
						: form.get('kind') === 'seed_book'
							? 'seed_book'
							: 'reset',
				confirm_alias: String(form.get('confirm_alias') ?? '')
			})
		});
		if (!res.ok) return fail(res.status, { dataError: await detail(res) });
		return { dataJob: await res.json() };
	},

	resumeProviders: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, { dataError: 'You need the tenants:write permission to resume providers.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/data-job/resume-providers`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { dataError: await detail(res) });
		return { providersResumed: await res.json() };
	},

	// Switch the firm's AI actions on or off and set its monthly token ceiling.
	//
	// OPERATIONS-SIDE ON PURPOSE. There is no firm self-service equivalent of this form: sending a
	// firm's clients' free text to a third-party model is Graphene's decision as platform provider,
	// the same posture that makes AML adjudication operations-only (docs/aml.md). It is the SECOND
	// of the feature's two switches — the first is ANTHROPIC_API_KEY on the deployment, which no
	// screen can set.
	setAi: async ({ params, locals, request, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, { aiError: 'You need the tenants:write permission to change this.' });
		// Resolved AFTER the write guard, deliberately: a read-only operator's action must not touch
		// the API at all, and this lookup is an API call. (The route is addressed by uid now, so the
		// numeric id the `/admin/tenants/{tenant_id}` endpoints take has to be looked up.)
		const form = await request.formData();
		const cap = String(form.get('monthly_token_cap') ?? '').trim();
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/ai`, {
			method: 'PUT',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify({
				enabled: form.get('enabled') === 'on',
				// Blank leaves an existing ceiling alone (the API's COALESCE), so switching a firm
				// off cannot silently reset a number somebody chose.
				monthly_token_cap: cap ? Number(cap) : null,
				notes: String(form.get('notes') ?? '').trim() || null
			})
		});
		if (!res.ok) return fail(res.status, { aiError: await detail(res) });
		return { ai: await res.json() };
	},

	// Who this environment may email. The list is the deployment's, shown on the firm page because
	// that is where an operator is when a test send goes somewhere it should not. Saving it changes
	// mail for every firm. Resetting puts the deployment default back.
	setEmailAllowlist: async ({ locals, request, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, {
				emailError: 'You need the tenants:write permission to change who email is sent to.'
			});
		const form = await request.formData();
		const useDefault = form.get('use_default') === '1';
		const entries = String(form.get('entries') ?? '')
			.split(/[\n,]/)
			.map((entry) => entry.trim())
			.filter(Boolean);
		const res = await fetch(`${apiUrl()}/admin/outbound-email`, {
			method: 'PUT',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify({ entries, use_default: useDefault })
		});
		if (!res.ok) return fail(res.status, { emailError: await detail(res) });
		return { emailAllowlist: (await res.json()) as OutboundEmailAllowlist };
	},

	// Rescan the bucket. A POST an operator triggers, never something the load does: totalling
	// a bucket is a full ListObjectsV2 walk, so an implicit refresh would put an unbounded S3
	// walk behind every render of this page.
	refreshUsage: async ({ params, locals, fetch }) => {
		const tenantUid = params.uid;
		if (!locals.user) redirect(303, '/login');
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/storage/usage/refresh`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { usageRefreshed: await res.json() };
	}
};
