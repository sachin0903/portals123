import { modelTenant } from '$lib/model';

/**
 * Which operations permission governs a tenant's email templates.
 *
 * The cross-tenant email-template door serves BOTH the model preset (the templates every new firm
 * clones) and a real firm's own templates, and it does not gate them the same way: the API decides
 * `model:*` for the model tenant and `tenants:*` for a firm (`admin.py::_require_tenant_scope`).
 * Mirroring that choice in the app is what stops the UI offering a page — or a proxy route — the
 * API will refuse.
 *
 * Lives here rather than in each route because five surfaces need the same answer (the list page,
 * the editor, and the preview / send-test / versions proxies) and a sixth copy is how they drift.
 * `modelTenant` failing to resolve degrades to the firm scope, which is the stricter of the two for
 * a non-model tenant and never widens access: the API re-checks regardless.
 */
export type TemplateScope = {
	read: 'model:read' | 'tenants:read';
	write: 'model:write' | 'tenants:write';
};

export async function templateScope(
	tenantUid: string,
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<TemplateScope> {
	// Compared on the UID, which is what the route carries — the serial was only ever a proxy for
	// "is this the model tenant", and reaching for it meant resolving the uid first.
	const modelUid = await modelTenant(accessToken, fetch)
		.then((m) => m.uid)
		.catch(() => null);
	return modelUid !== null && modelUid === tenantUid
		? { read: 'model:read', write: 'model:write' }
		: { read: 'tenants:read', write: 'tenants:write' };
}
