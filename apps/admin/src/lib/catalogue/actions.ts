import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions } from '@sveltejs/kit';

/** Form actions for every page under /platform. The section's data comes from
 * +layout.server.ts; these are the writes, posted to the catalogue API which owns the
 * rules (a fee rule matching its type, allocations totalling 100) so they are not
 * duplicated here.
 */

/** Every action posts JSON to the catalogue API and returns its 422 message as-is: the API owns
 * the rules (a fee rule matching its type, allocations totalling 100), so duplicating them here
 * would give two answers to the same question. */
async function send(
	fetch: typeof globalThis.fetch,
	token: string | null,
	method: 'POST' | 'PATCH' | 'PUT',
	path: string,
	body: unknown
) {
	const res = await fetch(path, {
		method,
		headers: jsonHeaders(token),
		body: JSON.stringify(body)
	});
	if (!res.ok) return { ok: false as const, error: await detail(res) };
	return { ok: true as const, data: await res.json() };
}

function guard(locals: App.Locals) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platforms:write')) error(403, 'Not authorised');
}

/** Form values arrive as strings; the API wants nulls for "not set" and real numbers for money. */
const str = (data: FormData, key: string): string | null => {
	const value = String(data.get(key) ?? '').trim();
	return value === '' ? null : value;
};
const num = (data: FormData, key: string): number | null => {
	const value = str(data, key);
	return value === null ? null : Number(value);
};
const flag = (data: FormData, key: string): boolean =>
	data.get(key) === 'on' || data.get(key) === 'true';

/** Only the keys the form actually CARRIED, so a partial form cannot clear what it does not
 *  render. The API writes every key it is given (`upsert_product` over `_PRODUCT_FIELDS`), and
 *  building a whole-shape body from a two-field form is how the models form came to wipe the VAT
 *  flag the platform had set — and how the products form's Offered switch came to clear
 *  `is_pension`, which since 0136 is what SHOWS the client record's Pension and Employer sections
 *  and the account's Beneficiaries panel. One toggle, and a firm's SIPP clients lose the section
 *  holding `relevant_uk_earnings`, the HMRC relievable-limit input. */
const carried = <T>(data: FormData, fields: Record<string, () => T>): Record<string, T> => {
	const body: Record<string, T> = {};
	for (const [key, read] of Object.entries(fields)) {
		if (data.has(key)) body[key] = read();
	}
	return body;
};

/** The firm these writes are aimed at.
 *
 * `catalogueActions` is shared by every page under /tenants/[uid]/platform, so SvelteKit types its
 * `params` as the generic record rather than one route's — which is why the uid needs narrowing
 * here and cannot just be passed straight through. A form action has no layout data to read, so
 * the path uid is taken from the route; an absent uid means the module was mounted outside the
 * section, which is a programming error rather than anything a user did.
 */
function targetTenant(params: Partial<Record<string, string>>): string {
	const uid = params.uid;
	if (!uid) error(500, 'catalogueActions mounted outside a /tenants/[uid] route');
	// The uid IS the address: `/admin/tenants/{tenant_uid}/catalogue/…` takes it directly, so what
	// used to be a `tenantIdFor` round trip per action is now the parameter already in hand.
	return uid;
}

export const catalogueActions: Actions = {
	product: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const uid = str(data, 'uid');
		const body: Record<string, unknown> = carried<unknown>(data, {
			product_type: () => str(data, 'product_type'),
			name: () => str(data, 'name'),
			code: () => str(data, 'code'),
			description: () => str(data, 'description'),
			currency: () => str(data, 'currency') ?? 'GBP',
			is_pension: () => flag(data, 'is_pension'),
			allowed_custodies: () => {
				const raw = str(data, 'allowed_custodies');
				return raw ? JSON.parse(raw) : [];
			},
			allowed_client_types: () => {
				const raw = str(data, 'allowed_client_types');
				return raw ? JSON.parse(raw) : [];
			},
			allowed_currencies: () => {
				const raw = str(data, 'allowed_currencies');
				return raw ? JSON.parse(raw) : [];
			},
			max_parties: () => num(data, 'max_parties'),
			min_age: () => num(data, 'min_age'),
			max_age: () => num(data, 'max_age'),
			bank_details_allowed: () => flag(data, 'bank_details_allowed'),
			enabled: () => flag(data, 'enabled'),
			sort_order: () => num(data, 'sort_order') ?? 0
		});
		// A CREATE has to state the two the API requires, whether or not a control rendered them.
		if (!uid) {
			body.product_type ??= str(data, 'product_type');
			body.name ??= str(data, 'name');
		}
		const base = `${apiUrl()}/admin/tenants/${tenantUid}/catalogue/products`;
		const result = uid
			? await send(fetch, locals.accessToken, 'PATCH', `${base}/${uid}`, body)
			: await send(fetch, locals.accessToken, 'POST', base, body);
		if (!result.ok) return fail(422, { error: result.error });
		// The enrolment template lives on the product×SEI mapping, not on the product row.
		// Saving it here (same form as the rest of the product settings) is the editor
		// docs/api-integrations.md §5.4 said was outstanding: one save, one judgement.
		const productUid = uid ?? (result.data as { uid?: string } | undefined)?.uid ?? null;
		const reference = str(data, 'account_open_reference');
		const hadSeiMapping = data.get('sei_mapped') === 'true';
		// No template and no existing SEI row: do not create an enabled mapping for
		// "not assigned". Clearing a template on a row that already exists still writes.
		if (productUid && data.has('account_open_reference') && (reference !== null || hadSeiMapping)) {
			const mapped = await send(
				fetch,
				locals.accessToken,
				'PUT',
				`${base}/${productUid}/platforms/sei`,
				{ account_open_reference: reference }
			);
			if (!mapped.ok) return fail(422, { error: mapped.error });
		}
		return { saved: 'product' };
	},

	productPlatform: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const productUid = str(data, 'product_uid');
		// The PROVIDER CODE, not a registry uid (0131). The route used to take
		// `platform.platforms.uid` so the URL carried no serial — the estate's addressing rule —
		// and the code is a better public identifier than the nanoid was: a person can read it.
		const provider = str(data, 'provider');
		if (!productUid || !provider)
			return fail(400, { error: 'Product and custodian are required.' });
		// Only the keys the form carried: a template save must not blank the pipeline's
		// external_id, and a rename must not blank the enrolment template. The API writes
		// every key it is given (`set_product_platform` treats absence as "not editing").
		const body = carried<unknown>(data, {
			external_id: () => str(data, 'external_id'),
			external_name: () => str(data, 'external_name'),
			account_open_reference: () => str(data, 'account_open_reference'),
			enabled: () => flag(data, 'enabled')
		});
		const result = await send(
			fetch,
			locals.accessToken,
			'PUT',
			`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/products/${productUid}/platforms/${provider}`,
			body
		);
		return result.ok ? { saved: 'reference' } : fail(422, { error: result.error });
	},

	model: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const uid = str(data, 'uid');
		const body = {
			name: str(data, 'name'),
			code: str(data, 'code'),
			aim: str(data, 'aim'),
			// The target return firms have been hiding in Seccl's `url` field, with the basis
			// that makes the number mean something.
			target_return: num(data, 'target_return'),
			target_return_basis: str(data, 'target_return_basis'),
			target_return_horizon_years: num(data, 'target_return_horizon_years'),
			benchmark: str(data, 'benchmark'),
			risk_rating: str(data, 'risk_rating'),
			risk_profile: str(data, 'risk_profile'),
			investment_decision: str(data, 'investment_decision'),
			model_charge: num(data, 'model_charge'),
			vatable: flag(data, 'vatable'),
			suspend_rebalances: flag(data, 'suspend_rebalances'),
			enabled: flag(data, 'enabled')
			// `currency` and `status` are deliberately NOT sent. The form has no control for either,
			// so they were posted as the hardcoded 'GBP'/'active' on every save — meaning editing a
			// model's name reset its status. The API only writes the keys it is given
			// (`_MODEL_FIELDS` filters on presence), so omitting them leaves the stored values
			// alone; the DB defaults still cover a create. `vatable` DOES have a switch now, which
			// is why it stays.
		};
		// The path takes the row's uid (0115). Posting the serial id looks up `WHERE uid = '42'`
		// and 404s as "Model not found" — which is how saving a target return used to fail.
		const base = `${apiUrl()}/admin/tenants/${tenantUid}/catalogue/models`;
		const result = uid
			? await send(fetch, locals.accessToken, 'PATCH', `${base}/${uid}`, body)
			: await send(fetch, locals.accessToken, 'POST', base, body);
		return result.ok ? { saved: 'model' } : fail(422, { error: result.error });
	},

	// A model's SEI DSR teams, edited on the model itself (moved off the SEI platform card). Writes
	// `config.model_dsrs[model_uid]` on the firm's SEI row; the slice config is left untouched.
	modelSeiDsrs: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const modelUid = str(data, 'model_uid');
		if (!modelUid) return fail(400, { error: 'Model is required.' });
		// The multi-select posts repeated hidden inputs; split on commas too so a manual value works.
		const dsrIds = [
			...new Set(
				data
					.getAll('dsr_ids')
					.flatMap((v) => String(v).split(','))
					.map((v) => v.trim())
					.filter(Boolean)
			)
		];
		const result = await send(
			fetch,
			locals.accessToken,
			'PUT',
			`${apiUrl()}/admin/tenants/${tenantUid}/providers/sei/models/${modelUid}/dsrs`,
			{ dsr_ids: dsrIds }
		);
		return result.ok ? { saved: 'model-dsrs' } : fail(422, { error: result.error });
	},

	modelVersion: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const modelUid = str(data, 'model_uid');
		let allocations: unknown;
		try {
			allocations = JSON.parse(String(data.get('allocations') ?? '[]'));
		} catch {
			return fail(400, { error: 'Invalid allocation payload.' });
		}
		const result = await send(
			fetch,
			locals.accessToken,
			'POST',
			`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/models/${modelUid}/versions`,
			{
				allocations,
				cash_allocation: num(data, 'cash_allocation') ?? 0,
				effective_from: str(data, 'effective_from'),
				note: str(data, 'note')
			}
		);
		return result.ok ? { saved: 'version' } : fail(422, { error: result.error });
	},

	publishVersion: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const result = await send(
			fetch,
			locals.accessToken,
			'POST',
			`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/models/${str(data, 'model_uid')}/versions/${str(data, 'version_uid')}/publish`,
			{}
		);
		return result.ok ? { saved: 'published' } : fail(422, { error: result.error });
	},

	commentary: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const modelUid = str(data, 'model_uid');
		const uid = str(data, 'uid');
		const body = {
			title: str(data, 'title'),
			body: str(data, 'body'),
			period_start: str(data, 'period_start'),
			period_end: str(data, 'period_end'),
			model_version_id: num(data, 'model_version_id'),
			publish: flag(data, 'publish')
		};
		const base = `${apiUrl()}/admin/tenants/${tenantUid}/catalogue/models/${modelUid}/commentary`;
		const result = uid
			? await send(fetch, locals.accessToken, 'PATCH', `${base}/${uid}`, body)
			: await send(fetch, locals.accessToken, 'POST', base, body);
		return result.ok ? { saved: 'commentary' } : fail(422, { error: result.error });
	},

	fee: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const uid = str(data, 'uid');
		let rule: unknown;
		try {
			rule = JSON.parse(String(data.get('rule') ?? '{}'));
		} catch {
			return fail(400, { error: 'Invalid fee rule.' });
		}
		const appliesTo = str(data, 'applies_to');
		const body = {
			name: str(data, 'name'),
			category: str(data, 'category'),
			description: str(data, 'description'),
			external_id: str(data, 'external_id'),
			frequency: str(data, 'frequency'),
			fee_type: str(data, 'fee_type'),
			rule,
			access: str(data, 'access') ?? 'hidden',
			charge_basis: str(data, 'charge_basis'),
			payer: str(data, 'payer'),
			vatable: flag(data, 'vatable'),
			minimum_amount: num(data, 'minimum_amount'),
			maximum_amount: num(data, 'maximum_amount'),
			applies_to: appliesTo
				? appliesTo
						.split(',')
						.map((v) => v.trim())
						.filter(Boolean)
				: null,
			effective_from: str(data, 'effective_from'),
			effective_to: str(data, 'effective_to'),
			currency: str(data, 'currency') ?? 'GBP',
			product_id: num(data, 'product_id'),
			model_id: num(data, 'model_id'),
			enabled: flag(data, 'enabled'),
			is_default: flag(data, 'is_default'),
			optional: flag(data, 'optional'),
			sort_order: num(data, 'sort_order') ?? 0
		};
		const base = `${apiUrl()}/admin/tenants/${tenantUid}/catalogue/fee-packages`;
		const result = uid
			? await send(fetch, locals.accessToken, 'PATCH', `${base}/${uid}`, body)
			: await send(fetch, locals.accessToken, 'POST', base, body);
		return result.ok ? { saved: 'fee' } : fail(422, { error: result.error });
	},

	provider: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const kind = str(data, 'kind');
		const uid = str(data, 'uid');
		if (kind !== 'third_party' && kind !== 'transfer') return fail(400, { error: 'Unknown kind.' });
		const body: Record<string, unknown> = {
			name: str(data, 'name'),
			description: str(data, 'description'),
			enabled: flag(data, 'enabled')
		};
		if (kind === 'transfer') {
			body.masterparty_id = str(data, 'masterparty_id');
			body.origo_id = str(data, 'origo_id');
		} else {
			// ONE wrapper, not a set of product ids (0130). The register states what it provides
			// (`data.wrapper_type`) and the pipeline maintains it; this is the firm's correction for
			// a provider the custodian does not publish. The old checkbox column wrote
			// `third_party_provider_products`, which was the same fact modelled twice and the copy
			// nobody read.
			body.product = str(data, 'product');
		}
		const base = `${apiUrl()}/admin/tenants/${tenantUid}/catalogue/providers/${kind}`;
		const result = uid
			? await send(fetch, locals.accessToken, 'PATCH', `${base}/${uid}`, body)
			: await send(fetch, locals.accessToken, 'POST', base, body);
		return result.ok ? { saved: 'provider' } : fail(422, { error: result.error });
	},

	term: async ({ request, params, locals, fetch }) => {
		guard(locals);
		const tenantUid = targetTenant(params);
		const data = await request.formData();
		const key = str(data, 'key');
		if (!key) return fail(400, { error: 'Term key is required.' });
		// A blank label clears the override — "reset to default" and an edit are the same call.
		const result = await send(
			fetch,
			locals.accessToken,
			'PUT',
			`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/terms/${key}`,
			{ label: str(data, 'label'), plural: str(data, 'plural') }
		);
		return result.ok ? { saved: 'term' } : fail(422, { error: result.error });
	}
};
