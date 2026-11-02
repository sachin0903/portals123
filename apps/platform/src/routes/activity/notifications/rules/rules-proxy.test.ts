/**
 * The firm notification-rule proxies, pinned on ONE thing: the body reaches the API whole.
 *
 * `variables` — the values a rule supplies to its email template — is the newest key on that body,
 * and a key silently dropped between the browser and `/firm/notification-rules` reads as "the
 * template rendered its fallbacks", not as a bug. So the assertions are on the exact key set the
 * proxy forwards, and on `null` surviving as `null`: an empty map must arrive as "supplies none"
 * and not as `{}`.
 */
import { describe, expect, it } from 'vitest';
import { POST } from './+server';
import { PATCH } from './[ruleUid]/+server';

const API = 'http://127.0.0.1:8000';

const locals = { user: { id: 'u1' }, accessToken: 'tok' };

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

function harness(status = 200) {
	const calls: { call: string; body: unknown; headers: unknown }[] = [];
	const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
		calls.push({
			call: `${init?.method ?? 'GET'} ${String(input)}`,
			body: init?.body,
			headers: init?.headers
		});
		return new Response(JSON.stringify({ uid: 'ntr_1' }), {
			status,
			headers: { 'content-type': 'application/json' }
		});
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch, sent: () => JSON.parse(String(calls[0].body)) };
}

const jsonRequest = (body: unknown) =>
	new Request('http://platform.test/activity/notifications/rules', {
		method: 'POST',
		body: JSON.stringify(body)
	});

/* eslint-disable @typescript-eslint/no-explicit-any */
const runPost = (args: object) => (POST as any)(args);
const runPatch = (args: object) => (PATCH as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('creating a firm notification rule', () => {
	it('forwards every field of the draft, the supplied variables included', async () => {
		const h = harness();
		const res = await runPost({ locals, request: jsonRequest(DRAFT), fetch: h.fetch });
		expect(res.status).toBe(200);
		expect(h.calls[0].call).toBe(`POST ${API}/firm/notification-rules`);
		expect(h.sent()).toEqual(DRAFT);
		expect(h.sent().variables).toEqual(DRAFT.variables);
	});

	it('passes "supplies none" through as null, not as an empty object', async () => {
		const h = harness();
		await runPost({ locals, request: jsonRequest({ ...DRAFT, variables: null }), fetch: h.fetch });
		expect(h.sent().variables).toBeNull();
		expect(Object.keys(h.sent())).toContain('variables');
	});

	it('refuses before calling the API when nobody is signed in', async () => {
		const h = harness();
		const res = await runPost({
			locals: { user: null, accessToken: null },
			request: jsonRequest(DRAFT),
			fetch: h.fetch
		});
		expect(res.status).toBe(401);
		expect(h.calls).toEqual([]);
	});
});

describe('editing a firm notification rule', () => {
	it('forwards the variables to the rule’s own PATCH', async () => {
		const h = harness();
		await runPatch({
			locals,
			params: { ruleUid: 'ntr_1' },
			request: jsonRequest({ ...DRAFT, uid: 'ntr_1' }),
			fetch: h.fetch
		});
		expect(h.calls[0].call).toBe(`PATCH ${API}/firm/notification-rules/ntr_1`);
		expect(h.sent().variables).toEqual(DRAFT.variables);
		expect(JSON.stringify(h.calls[0].headers)).toContain('tok');
	});

	it('surfaces the API’s refusal status rather than reporting a save', async () => {
		const h = harness(422);
		const res = await runPatch({
			locals,
			params: { ruleUid: 'ntr_1' },
			request: jsonRequest(DRAFT),
			fetch: h.fetch
		});
		expect(res.status).toBe(422);
	});
});
