import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import {
	subfirmIdsFromCustodyOrgs,
	UNASSIGNED_ORG,
	type Custody
} from '$lib/platforms/custody-orgs';
import type { PlatformStatus } from '$lib/platforms/registry';
import type { Actions, PageServerLoad } from './$types';

export type TenantProvider = {
	id: number;
	provider: 'morgan_lloyd' | 'seccl' | 'sei';
	config: Record<string, unknown>;
	enabled: boolean;
	/** Stamped by the MERGE (`book_sink.merge`), not by a fan-out — the column outlived the step
	 *  it was named for. Renaming it is an expand/contract migration, so the wire name stays. */
	last_fanout_at: string | null;
	/** Set when this firm is enabled but has no ACTIVE storage row — `book_sink.tenants` INNER
	 *  JOINs one, so without it the firm registers no models and nothing runs for it. */
	storage_missing?: string;
};

export type ProviderSource = { external_id: string; name: string };

/** One selectable upstream id, read live from the core warehouse. `parent` is the
 *  owning firm, which is what the SEI/Seccl cards cascade on. */
export type ProviderOption = {
	value: string;
	label: string;
	hint: string | null;
	parent: string | null;
};
/** Per provider: option group (`firms`, `subfirms`, `dsr_teams`, `nodes`) -> options. */
export type ProviderOptions = Record<string, ProviderOption[]>;

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The firm's uid — what `/admin/tenants/{tenant_uid}` takes. It is the route's own parameter,
	// so nothing is resolved to get it.
	const tenantUid = params.uid;
	const headers = authHeaders(locals.accessToken);

	// WHICH PROVIDERS EXIST IS THE API's ANSWER. This file used to declare its own
	// `['morgan_lloyd', 'seccl', 'sei']` tuple and pair it with a `PROVIDER_DEFS` module of names
	// and descriptions — two of the seven hard-coded copies of the same registry, and the reason
	// Seccl was described three different ways depending on which page you were on.
	const registryRes = await fetch(`${apiUrl()}/admin/platforms`, { headers });
	const registry: PlatformStatus[] = registryRes.ok ? await registryRes.json() : [];
	const keys = registry.map((entry) => entry.provider);

	// NO `…/sources` FETCH. 0140 folded `platform.providers` into `tenant_platforms`, so
	// registering a connection and enabling a firm on it are one step and that route is gone —
	// but this loader kept calling it once per provider, so every page load spent three 404 round
	// trips and set `sources[provider] = []`. The failure was silent and expensive: with both
	// feeds empty, `ProviderCard`'s Morgan Lloyd picker degraded to a free-text GUID box for the
	// one config key `_CONFIG_KEYS['morgan-lloyd']` marks required, and an empty dropdown reads as
	// "this firm has nothing to pick".
	const [providersRes, ...optionRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${tenantUid}/providers`, { headers }),
		...keys.map((p) => fetch(`${apiUrl()}/admin/providers/${p}/options`, { headers }))
	]);
	const providers: TenantProvider[] = providersRes.ok ? await providersRes.json() : [];
	const sources: Record<string, ProviderSource[]> = {};
	const options: Record<string, ProviderOptions> = {};
	for (const [i, provider] of keys.entries()) {
		// Kept as an empty list rather than removed: `ProviderCard` renders a list here and an
		// absent key would render `undefined`, which is the reason `overview()` keeps its own
		// `sources` key empty too.
		sources[provider] = [];
		// An options fetch that fails degrades to manual id entry, which is the documented
		// fallback: a provider can be configured before its models have ever run.
		options[provider] = optionRes[i]!.ok ? await optionRes[i]!.json() : {};
	}
	return { registry, providers, sources, options };
};

/** Every value under a field name, split on commas so the manual-entry fallback
 *  ("a, b, c") and the multi-select's repeated hidden inputs both work. */
function ids(data: FormData, ...names: string[]): string[] {
	const values = names.flatMap((name) => data.getAll(name));
	return [
		...new Set(
			values
				.flatMap((v) => String(v).split(','))
				.map((v) => v.trim())
				.filter(Boolean)
		)
	];
}

/** The slice-filter config from the card's form, shaped per provider. Optional
 *  narrowing keys are omitted when empty — the API reads an absent key as "don't
 *  narrow on that axis", whereas an empty list is rejected as invalid. */
function configFrom(provider: string, data: FormData): Record<string, unknown> {
	if (provider === 'sei') {
		const config: Record<string, unknown> = { firm_id: String(data.get('firm_id') ?? '').trim() };
		const individualTemplateId = String(data.get('client_template_individual_id') ?? '').trim();
		const organisationTemplateId = String(data.get('client_template_organisation_id') ?? '').trim();
		if (individualTemplateId && individualTemplateId !== UNASSIGNED_ORG) {
			config.client_template_individual_id = individualTemplateId;
			// The individual choice is also the legacy single key, so a reader that
			// still looks for one template opens a person.
			config.client_template_id = individualTemplateId;
		}
		if (organisationTemplateId && organisationTemplateId !== UNASSIGNED_ORG) {
			config.client_template_organisation_id = organisationTemplateId;
		}
		const dsrIds = ids(data, 'dsr_ids');
		if (dsrIds.length) config.dsr_ids = dsrIds;
		const custodyOrgs: Partial<Record<Custody, string>> = {};
		const custodyDsrs: Partial<Record<Custody, string[]>> = {};
		for (const key of ['onshore', 'offshore'] as const) {
			const id = String(data.get(`custody_org_${key}`) ?? '').trim();
			if (id && id !== UNASSIGNED_ORG) custodyOrgs[key] = id;
			const teams = ids(data, `custody_dsr_${key}`);
			if (teams.length) custodyDsrs[key] = teams;
		}
		const subfirmIds = subfirmIdsFromCustodyOrgs(custodyOrgs);
		if (subfirmIds.length) {
			config.custody_orgs = custodyOrgs;
			config.subfirm_ids = subfirmIds;
		}
		if (Object.keys(custodyDsrs).length) config.custody_dsrs = custodyDsrs;
		// A model's SEI DSR teams (`model_dsrs`) are edited on the model itself now (the catalogue's
		// Models editor), not here; the card no longer sends them and `set_provider` preserves them.
		// The custodian's collection bank account, shown in the account payment drawer. A blank
		// control omits the key so `_FORM_KEYS` clears it rather than storing an empty string.
		for (const key of [
			'pay_in_account_name',
			'pay_in_sort_code',
			'pay_in_account_number'
		] as const) {
			const value = String(data.get(key) ?? '').trim();
			if (value) config[key] = value;
		}
		return config;
	}
	const picked = ids(data, 'source', 'manual');
	return { [provider === 'seccl' ? 'node_ids' : 'tenant_ids']: picked };
}

export const actions: Actions = {
	// Enable/update/disable a platform for this firm; enabling fans its slice out
	// into the firm's lake immediately.
	setProvider: async ({ params, locals, fetch, request }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const provider = String(data.get('provider') ?? '');
		// Enabling a platform fans a warehouse slice into the firm's lake — a write. The page
		// renders for tenants:read, so the action refuses read-only operators itself (the API
		// gates it too); the cards disable their controls off the layout's canManage.
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, {
				provider,
				providerError: 'You need the tenants:write permission to change platform enablement.'
			});
		const tenantUid = params.uid;
		const enabled = data.get('enabled') === 'true';
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/providers/${encodeURIComponent(provider)}`,
			{
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ config: configFrom(provider, data), enabled })
			}
		);
		if (!res.ok) return fail(res.status, { provider, providerError: await detail(res) });
		return { provider, providerSet: await res.json() };
	},

	// Re-run just this firm's pipeline models on the provider's daemon: filter -> stage -> merge.
	// A write in the same sense enabling is (it lands rows in the firm's book), so it takes the
	// same permission.
	refreshProvider: async ({ params, locals, fetch, request }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const provider = String(data.get('provider') ?? '');
		if (!can(locals.permissions, locals.role, 'tenants:write'))
			return fail(403, {
				provider,
				providerError: "You need the tenants:write permission to refresh a firm's data."
			});
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${params.uid}/providers/${encodeURIComponent(provider)}/refresh`,
			{ method: 'POST', headers: jsonHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { provider, providerError: await detail(res) });
		const body = await res.json();
		// The message is `registry.refreshMessage`'s, shared with the platform page's own per-firm
		// Refresh: one endpoint, one explanation.
		return {
			provider,
			refreshed: body
		};
	}
};
