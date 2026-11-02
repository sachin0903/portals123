/**
 * Shared scaffolding for the firm's own email-template surfaces (the list actions, the edit loader and
 * the preview / send-test / versions proxies), which all need the same signed-in staff principal, the
 * same JSON responder and the same recording fetch. Not a test file — no `.test.ts` suffix, so vitest
 * does not collect it.
 */

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json', ...headers }
	});

/** A signed-in adviser holding `email_templates:write` (which implies read). */
export const staff = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['email_templates:write'],
	role: 'adviser'
} as unknown as App.Locals;

/** The same adviser with a different permission set. */
export const withPermissions = (permissions: string[]) =>
	({ ...staff, permissions }) as unknown as App.Locals;

/** Signed out. */
export const anonymous = { ...staff, user: null } as unknown as App.Locals;

type Call = { url: string; method: string; body: unknown; headers: unknown };

/** Records every call the handler makes and answers from `routes`. */
export function harness(routes: (url: string, init?: RequestInit) => Response) {
	const calls: Call[] = [];
	const fetch = (async (url: string | URL, init?: RequestInit) => {
		const u = String(url);
		calls.push({ url: u, method: init?.method ?? 'GET', body: init?.body, headers: init?.headers });
		return routes(u, init);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		fetch,
		/** The JSON body of the first call made with this method — what the API actually received. */
		sent: (method = 'POST') => JSON.parse(String(calls.find((c) => c.method === method)!.body)),
		made: (method: string) => calls.some((c) => c.method === method)
	};
}

/** A POST carrying form fields, as `use:enhance` sends it to a page action. */
export const formRequest = (fields: Record<string, string>) => {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.set(k, v);
	return new Request('http://portal.test/email-templates', { method: 'POST', body });
};

/** A POST carrying a JSON body, as the editor sends it to a proxy. */
export const jsonRequest = (body: unknown) =>
	new Request('http://portal.test/email-templates/eml_1/x', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
