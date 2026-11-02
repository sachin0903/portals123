/**
 * The operations notification-rule proxies. Two hops matter here: the route's uid becomes the
 * numeric tenant id the `/admin/tenants/{tenant_id}` contract needs, and the body reaches the API
 * whole.
 *
 * `variables` — the values a rule supplies to its email template — is the newest key on that body,
 * and a key silently dropped between the browser and the API reads as "the template rendered its
 * fallbacks", not as a bug. So the assertions are on the exact key set forwarded, and on `null`
 * surviving as `null`: an empty map must arrive as "supplies none" and not as `{}`.
 */
import { describe, expect, it } from 'vitest';
import { POST } from './+server';
import { PATCH } from './[ruleUid]/+server';

const API = 'http://127.0.0.1:8000';
const FIRM = { id: 7, uid: 'ten_firm' };

const locals = { user: { id: 'u-ops' }, accessToken: 'tok', permissions: ['tenants:write'] };

const DRAFT = {
	trigger_key: 'document.shared',
	audience: 'adviser',
	label: 'A document is shared',
	title: 'New document for {client.name}',
	body: '',
	link: '/documents',
	default_in_app: true,
	default_email: true,
	mandatory: false,
	firm_editable: true,
	enabled: true,
	email_code: 'document_shared',
	variables: { button_label: 'Review it', button_url: '/documents/{document.uid}' }
};

const okJson = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** Answers the uid→id lookup, and records only the rule calls — a refusal has to resolve the
 *  tenant first, so a zero TOTAL fetch count would be the wrong assertion. */
function harness(status = 200) {
	const calls: { call: string; body: unknown; headers: unknown }[] = [];
	const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
		const url = String(input);
		calls.push({
			call: `${init?.method ?? 'GET'} ${url}`,
			body: init?.body,
			headers: init?.headers
		});
		return okJson({ uid: 'ntr_1' }, status);
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch, sent: () => JSON.parse(String(calls[0].body)) };
}

const jsonRequest = (body: unknown) =>
	new Request('http://admin.test/x', { method: 'POST', body: JSON.stringify(body) });

/* eslint-disable @typescript-eslint/no-explicit-any */
const runPost = (args: object) => (POST as any)(args);
const runPatch = (args: object) => (PATCH as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('creating a rule for a firm', () => {
	it('forwards every field to the uid-addressed endpoint, the supplied variables included', async () => {
		const h = harness();
		const res = await runPost({
			locals,
			params: { uid: FIRM.uid },
			request: jsonRequest(DRAFT),
			fetch: h.fetch
		});
		expect(res.status).toBe(200);
		expect(h.calls[0].call).toBe(`POST ${API}/admin/tenants/${FIRM.uid}/notification-rules`);
		expect(h.sent()).toEqual(DRAFT);
	});

	it('passes "supplies none" through as null, not as an empty object', async () => {
		const h = harness();
		await runPost({
			locals,
			params: { uid: FIRM.uid },
			request: jsonRequest({ ...DRAFT, variables: null }),
			fetch: h.fetch
		});
		expect(h.sent().variables).toBeNull();
		expect(Object.keys(h.sent())).toContain('variables');
	});

	it('refuses before resolving anything when nobody is signed in', async () => {
		const h = harness();
		const res = await runPost({
			locals: { user: null, accessToken: null },
			params: { uid: FIRM.uid },
			request: jsonRequest(DRAFT),
			fetch: h.fetch
		});
		expect(res.status).toBe(401);
		expect(h.calls).toEqual([]);
	});
});

describe('editing a rule for a firm', () => {
	it('forwards the variables to the rule’s own PATCH', async () => {
		const h = harness();
		await runPatch({
			locals,
			params: { uid: FIRM.uid, ruleUid: 'ntr_1' },
			request: jsonRequest({ ...DRAFT, uid: 'ntr_1' }),
			fetch: h.fetch
		});
		expect(h.calls[0].call).toBe(`PATCH ${API}/admin/tenants/${FIRM.uid}/notification-rules/ntr_1`);
		expect(h.sent().variables).toEqual(DRAFT.variables);
		expect(JSON.stringify(h.calls[0].headers)).toContain('tok');
	});

	it('surfaces the API’s refusal status rather than reporting a save', async () => {
		const h = harness(422);
		const res = await runPatch({
			locals,
			params: { uid: FIRM.uid, ruleUid: 'ntr_1' },
			request: jsonRequest(DRAFT),
			fetch: h.fetch
		});
		expect(res.status).toBe(422);
	});
});
