import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

const read = (token: string | null, path: string, fetch: typeof globalThis.fetch) =>
	fetch(`${apiUrl()}${path}`, { headers: authHeaders(token) });

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platforms:read')) redirect(303, '/');
	const [growth, allowances, custody, costs] = await Promise.all([
		read(locals.accessToken, '/admin/illustration-reference/growth-sets', fetch),
		read(locals.accessToken, '/admin/illustration-reference/isa-allowances', fetch),
		read(locals.accessToken, '/admin/illustration-reference/custody', fetch),
		read(locals.accessToken, '/admin/illustration-reference/instrument-costs', fetch)
	]);
	return {
		canWrite: can(locals.permissions, locals.role, 'platforms:write'),
		growthSets: growth.ok ? await growth.json() : [],
		allowances: allowances.ok ? await allowances.json() : [],
		custody: custody.ok ? await custody.json() : [],
		costs: costs.ok ? await costs.json() : []
	};
};

const post = async (
	locals: App.Locals,
	fetch: typeof globalThis.fetch,
	path: string,
	body: unknown,
	action: string
) => {
	if (!can(locals.permissions, locals.role, 'platforms:write')) {
		return fail(403, {
			action,
			error: 'You need platforms:write to change illustration reference data.'
		});
	}
	const res = await fetch(`${apiUrl()}${path}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return fail(res.status, { action, error: await detail(res) });
	return { action, ok: true };
};

export const actions: Actions = {
	growth: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		return post(
			locals,
			fetch,
			'/admin/illustration-reference/growth-sets',
			{
				effective_from: String(data.get('effective_from') ?? ''),
				equity_rate: data.get('equity_rate'),
				gilts_rate: data.get('gilts_rate'),
				bonds_rate: data.get('bonds_rate'),
				other_rate: data.get('other_rate'),
				spread: data.get('spread') || '0.03',
				gia_haircut: data.get('gia_haircut') || '0.005',
				inflation: data.get('inflation') || '0.02',
				approved: data.get('approved') === 'on'
			},
			'growth'
		);
	},
	allowance: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		return post(
			locals,
			fetch,
			'/admin/illustration-reference/isa-allowances',
			{
				tax_year: String(data.get('tax_year') ?? '').trim(),
				allowance: data.get('allowance'),
				effective_from: String(data.get('effective_from') ?? '')
			},
			'allowance'
		);
	},
	custody: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		return post(
			locals,
			fetch,
			'/admin/illustration-reference/custody',
			{
				provider: String(data.get('provider') ?? ''),
				effective_from: String(data.get('effective_from') ?? ''),
				cash_paid_rate: data.get('cash_paid_rate') || '0',
				cash_retention_rate: data.get('cash_retention_rate') || '0',
				custodian_name: String(data.get('custodian_name') ?? '').trim(),
				custodian_fca: String(data.get('custodian_fca') ?? '').trim()
			},
			'custody'
		);
	},
	costs: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		let rows: unknown;
		try {
			rows = JSON.parse(String(data.get('rows') ?? '[]'));
		} catch {
			return fail(400, { action: 'costs', error: 'Cost import must be a JSON list.' });
		}
		if (!Array.isArray(rows)) {
			return fail(400, { action: 'costs', error: 'Cost import must be a JSON list.' });
		}
		return post(locals, fetch, '/admin/illustration-reference/instrument-costs', rows, 'costs');
	}
};
