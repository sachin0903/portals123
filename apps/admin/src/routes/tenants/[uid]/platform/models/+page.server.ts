import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

// The section's data is loaded once by ../+layout.server.ts; these are the shared writes.
export { catalogueActions as actions } from '$lib/catalogue/actions';

type ProviderRow = { provider: string; enabled: boolean; config?: Record<string, unknown> };
type Option = { value: string; label: string; hint: string | null; parent: string | null };

/** The firm's SEI DSR teams, so a model's DSR assignment is edited on the model itself — it used
 *  to be a per-model list on the SEI platform card. Only SEI carries this, so nothing is fetched
 *  for a firm without it. Loaded here, not in the shared /platform layout, so the other pages in
 *  the section do not pay for a provider read they never use. */
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	const headers = authHeaders(locals.accessToken);
	const [providersRes, optionsRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/providers`, { headers }),
		fetch(`${apiUrl()}/admin/providers/sei/options`, { headers })
	]);
	const providers: ProviderRow[] = providersRes.ok ? await providersRes.json() : [];
	const sei = providers.find((p) => p.provider === 'sei');
	const config = (sei?.config ?? {}) as Record<string, unknown>;
	const firmId = typeof config.firm_id === 'string' ? config.firm_id : '';
	const options = optionsRes.ok ? await optionsRes.json() : {};
	const dsrTeams: Option[] = Array.isArray(options.dsr_teams) ? options.dsr_teams : [];
	// Narrowed to the chosen firm's teams, the same rule the card used (rows with no parent kept).
	const seiDsrOptions = dsrTeams
		.filter((row) => !firmId || row.parent === firmId || !row.parent)
		.map(({ value, label }) => ({ value, label }));
	// The current per-model assignment, read off the SEI config's `model_dsrs` map.
	const raw = config.model_dsrs;
	const modelDsrs: Record<string, string[]> = {};
	if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
		for (const [uid, value] of Object.entries(raw as Record<string, unknown>)) {
			if (Array.isArray(value)) modelDsrs[uid] = value.map(String);
		}
	}
	return { seiEnabled: Boolean(sei?.enabled), seiDsrOptions, modelDsrs };
};
