/**
 * Shared scaffolding for the cross-tenant email-template surfaces (the list actions, the three proxies
 * and the edit loader). Every one of them asks the same two questions before it does anything — resolve
 * the route's uid to the numeric id the API's `/admin/tenants/{tenant_id}` contract needs, and find out
 * whether the target IS the model preset, because the target tenant decides the permission family
 * (`model:*` for the preset, `tenants:*` for a firm, mirroring the API's `_require_tenant_scope`).
 *
 * Not a test file itself — no `.test.ts` suffix, so vitest does not collect it.
 */

type GateLocals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};

/** An operations principal holding exactly these permissions. */
export const locals = (permissions: readonly string[]): GateLocals => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions: [...permissions],
	role: null
});

/** Anonymous: every door must refuse before resolving anything. */
export const anonymous = (): GateLocals => ({ ...locals([]), user: null });

export const FIRM = { id: 7, uid: 'ten_firm', name: 'Northwood Wealth' };
export const MODEL = { id: 1, uid: 'ten_model', name: 'Model Tenant' };

export const okJson = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json', ...headers }
	});

/**
 * Answers the model lookup and records ONLY the calls that matter — the email-template endpoints.
 * A refusal still has to know which permission family the target falls under, and that is decided
 * by comparing it with the model tenant, so that read is answered and not recorded.
 */
export function tenantApi(
	tenant: { id: number; uid: string },
	respond: (url: string, init?: RequestInit) => Response = () => okJson({ uid: 'tpl_1' })
) {
	const calls: { call: string; url: string; body: unknown; headers: unknown }[] = [];
	const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
		const url = String(input);
		// No by-uid interception: the endpoints take the uid the route carries, so a proxy makes
		// no resolution call at all. `/admin/model` stays — the SCOPE check still asks which
		// permission family a tenant falls under, and that is a comparison against the model.
		if (url.endsWith('/admin/model')) return okJson(MODEL);
		calls.push({
			call: `${init?.method ?? 'GET'} ${url}`,
			url,
			body: init?.body,
			headers: init?.headers
		});
		return respond(url, init);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		fetch,
		/** `METHOD url` per recorded call, which is what the assertions read. */
		made: () => calls.map((c) => c.call),
		/** The first recorded call's JSON body. */
		sent: () => JSON.parse(String(calls[0].body))
	};
}

/** A POST carrying a JSON body, as the browser sends it to a proxy. */
export const jsonRequest = (body: unknown) =>
	new Request('http://admin.test/x', { method: 'POST', body: JSON.stringify(body) });

/** A POST carrying form fields, as `use:enhance` sends it to a page action. */
export const formRequest = (fields: Record<string, string>) => {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.set(k, v);
	return new Request('http://admin.test/tenants/ten_firm/email-templates', {
		method: 'POST',
		body
	});
};

/** The API base every assertion spells out, so a changed host shows up in one place. */
export const API = 'http://127.0.0.1:8000';
