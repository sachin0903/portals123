/**
 * The flows listing's `?/import` action: the gate, what it refuses before the API sees anything,
 * and the body it posts. The API owns the real work (`tests/test_flows_portability.py`); this
 * only has to read the file faithfully and hand back what the sheet shows.
 */
import { describe, expect, it, vi } from 'vitest';
import { parseBundle } from '$lib/flow-bundle';
import { actions } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const BUNDLE = {
	format: 'graphene.flow',
	version: 1,
	flow: { code: 'fact_find', name: 'Fact find', description: null, entity: 'advice' },
	definition: { nodes: [] },
	refs: { forms: {}, templates: {}, terms: {} },
	forms: [],
	templates: []
};

const IMPORTED = {
	flow: { uid: 'flw_new', code: 'fact_find', name: 'Fact find', status: 'draft' },
	created: true,
	version_uid: 'ver_1',
	forms: { created: ['advice_fact_find'], reused: [] },
	templates: { created: [], reused: [] },
	warnings: ['“Letter” hands out a stored document, which does not travel with a flow.']
};

function harness(handler: (url: string, init?: RequestInit) => Response) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: unknown, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		return handler(url, init);
	});
	return { fetch, calls };
}

type ActionArg = Parameters<typeof actions.import>[0];

function event(
	fetch: ReturnType<typeof harness>['fetch'],
	body: FormData,
	permissions: string[] = ['flows:write']
): ActionArg {
	return {
		request: new Request('http://portal.test/flows?/import', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	} as unknown as ActionArg;
}

function upload(contents: string, code = ''): FormData {
	const body = new FormData();
	body.set('bundle', new File([contents], 'fact_find.flow.json', { type: 'application/json' }));
	body.set('code', code);
	return body;
}

describe('parseBundle', () => {
	it('reads an object and refuses anything else', () => {
		expect(parseBundle(JSON.stringify(BUNDLE))).toEqual(BUNDLE);
		expect(parseBundle('[]')).toBeNull();
		expect(parseBundle('"text"')).toBeNull();
		expect(parseBundle('not json')).toBeNull();
	});
});

describe('?/import', () => {
	it('needs flows:write', async () => {
		const { fetch, calls } = harness(() => json(IMPORTED, 201));
		const result = await actions.import(
			event(fetch, upload(JSON.stringify(BUNDLE)), ['flows:read'])
		);
		expect(result).toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});

	it('refuses an empty or unreadable file before calling the API', async () => {
		const { fetch, calls } = harness(() => json(IMPORTED, 201));
		const none = new FormData();
		none.set('code', '');
		expect(await actions.import(event(fetch, none))).toMatchObject({ status: 400 });
		expect(await actions.import(event(fetch, upload('not json')))).toMatchObject({
			status: 400
		});
		expect(calls).toHaveLength(0);
	});

	it('posts the bundle and the chosen code, and returns what the sheet shows', async () => {
		const { fetch, calls } = harness(() => json(IMPORTED, 201));
		const result = await actions.import(event(fetch, upload(JSON.stringify(BUNDLE), 'our_ff')));
		expect(calls).toHaveLength(1);
		expect(calls[0].url).toMatch(/\/flows\/import$/);
		expect(JSON.parse(String(calls[0].init?.body))).toEqual({ bundle: BUNDLE, code: 'our_ff' });
		expect(result).toEqual({
			ok: true,
			uid: 'flw_new',
			name: 'Fact find',
			imported: {
				created: true,
				forms: IMPORTED.forms,
				templates: IMPORTED.templates,
				warnings: IMPORTED.warnings
			}
		});
	});

	it('sends no code when the author left it blank', async () => {
		const { fetch, calls } = harness(() => json(IMPORTED, 201));
		await actions.import(event(fetch, upload(JSON.stringify(BUNDLE))));
		expect(JSON.parse(String(calls[0].init?.body)).code).toBeNull();
	});

	it('surfaces the API refusal', async () => {
		const { fetch } = harness(() => json({ detail: 'This file is not a flow export.' }, 422));
		const result = await actions.import(event(fetch, upload(JSON.stringify(BUNDLE))));
		expect(result).toMatchObject({
			status: 422,
			data: { error: 'This file is not a flow export.' }
		});
	});
});
