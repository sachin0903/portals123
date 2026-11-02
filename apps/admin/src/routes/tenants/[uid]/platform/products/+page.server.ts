import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { enrollmentTemplatesForFirm } from '$lib/catalogue/enrollment';
import type { ProviderOption } from '$lib/catalogue/types';
import type { PageServerLoad } from './$types';

type CurrencySettings = {
	base_currency: string;
	allowed_currencies: string[];
	currencies: { code: string; symbol: string | null; currency: string | null }[];
};

type TenantProvider = {
	provider: string;
	enabled: boolean;
	config: Record<string, unknown>;
};

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const headers = authHeaders(locals.accessToken);
	const [currencyRes, optionsRes, providersRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/currency-settings`, { headers }),
		fetch(`${apiUrl()}/admin/providers/sei/options`, { headers }),
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/providers`, { headers })
	]);
	if (!currencyRes.ok) error(currencyRes.status, await detail(currencyRes));
	const options: Record<string, ProviderOption[]> = optionsRes.ok ? await optionsRes.json() : {};
	const providers: TenantProvider[] = providersRes.ok ? await providersRes.json() : [];
	const sei = providers.find((row) => row.provider === 'sei' && row.enabled);
	const firmIdRaw = sei?.config.firm_id;
	const firmId =
		typeof firmIdRaw === 'string' || typeof firmIdRaw === 'number'
			? String(firmIdRaw).trim() || null
			: null;
	return {
		currencySettings: (await currencyRes.json()) as CurrencySettings,
		seiAccountTemplates: enrollmentTemplatesForFirm(options.account_templates ?? [], firmId),
		seiFirmId: firmId
	};
};

// The catalogue itself is loaded once by ../+layout.server.ts; these are the shared writes.
export { catalogueActions as actions } from '$lib/catalogue/actions';
