/**
 * The client page's loader and its acts. Every act names the check it is on, because the page shows
 * several; the loader moves a contact key to the client's lasting key and opens an earlier check
 * only when it is this client's own.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';
import { load as checkRedirect } from '../../[uid]/+page.server';

const locals = (permissions: string[] = ['aml:read', 'aml:write', 'aml:adjudicate']) =>
	({
		user: { id: 'u-ops' },
		accessToken: 'tok',
		permissions,
		role: null
	}) as never;

type Call = { url: string; method: string; body: unknown };

/** A fetch that answers by URL suffix, recording every call. */
function api(answers: Record<string, unknown>) {
	const calls: Call[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		const href = String(url);
		calls.push({ url: href, method: init?.method ?? 'GET', body: init?.body });
		const match = Object.keys(answers)
			.sort((a, b) => b.length - a.length)
			.find((suffix) => href.split('?')[0].endsWith(suffix));
		if (!match) return new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 });
		const answer = answers[match];
		return answer instanceof Response ? answer : new Response(JSON.stringify(answer));
	}) as unknown as typeof globalThis.fetch;
	return { calls, fetch };
}

function page(key: string, earlier: { uid: string }[] = []) {
	return {
		key,
		client: { name: 'Ada', origin: 'portal' },
		awaiting_screen: false,
		stale: false,
		needs_attention: false,
		risk: null,
		identity: null,
		aml: null,
		earlier
	};
}

function form(fields: Record<string, string>) {
	const body = new FormData();
	for (const [k, v] of Object.entries(fields)) body.append(k, v);
	return new Request('http://admin.test/aml/clients/cl_1', { method: 'POST', body });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const run = (fn: unknown, event: Record<string, unknown>) => (fn as any)(event);

describe('the client page loader', () => {
	it('loads the client by its key, and only reads', async () => {
		const { fetch, calls } = api({ '/aml/clients/cl_1': page('cl_1') });
		const data = await run(load, {
			params: { key: 'cl_1' },
			url: new URL('http://admin.test/aml/clients/cl_1'),
			locals: locals(),
			fetch
		});
		expect(data.page.key).toBe('cl_1');
		expect(data.earlier).toBeNull();
		expect(data.canWrite).toBe(true);
		expect(data.canAdjudicate).toBe(true);
		expect(calls.map((c) => c.url)).toEqual([expect.stringMatching(/\/aml\/clients\/cl_1$/)]);
		expect(calls.every((c) => c.method === 'GET')).toBe(true);
	});

	it('gives a reader the page read only', async () => {
		const { fetch } = api({ '/aml/clients/cl_1': page('cl_1') });
		const data = await run(load, {
			params: { key: 'cl_1' },
			url: new URL('http://admin.test/aml/clients/cl_1'),
			locals: locals(['aml:read']),
			fetch
		});
		expect(data.page.key).toBe('cl_1');
		expect(data.canWrite).toBe(false);
		expect(data.canAdjudicate).toBe(false);
	});

	it('moves a contact key to the lasting key once the client has been screened', async () => {
		const { fetch } = api({ '/aml/clients/contact-c1': page('cl_9') });
		await expect(
			run(load, {
				params: { key: 'contact-c1' },
				url: new URL('http://admin.test/aml/clients/contact-c1?check=chk_2'),
				locals: locals(),
				fetch
			})
		).rejects.toMatchObject({ status: 303, location: '/aml/clients/cl_9?check=chk_2' });
	});

	it('opens an earlier check of this client, and ignores any other uid', async () => {
		const own = api({
			'/aml/clients/cl_1': page('cl_1', [{ uid: 'chk_old' }]),
			'/aml/checks/chk_old': { check: { uid: 'chk_old', kind: 'aml' } }
		});
		const opened = await run(load, {
			params: { key: 'cl_1' },
			url: new URL('http://admin.test/aml/clients/cl_1?check=chk_old'),
			locals: locals(),
			fetch: own.fetch
		});
		expect(opened.earlier.check.uid).toBe('chk_old');

		const other = api({ '/aml/clients/cl_1': page('cl_1', [{ uid: 'chk_old' }]) });
		const ignored = await run(load, {
			params: { key: 'cl_1' },
			url: new URL('http://admin.test/aml/clients/cl_1?check=chk_elsewhere'),
			locals: locals(),
			fetch: other.fetch
		});
		expect(ignored.earlier).toBeNull();
		expect(other.calls.some((c) => c.url.includes('/aml/checks/'))).toBe(false);
	});

	it('is a 404 for a client the console does not know', async () => {
		const { fetch } = api({});
		await expect(
			run(load, {
				params: { key: 'nobody' },
				url: new URL('http://admin.test/aml/clients/nobody'),
				locals: locals(),
				fetch
			})
		).rejects.toMatchObject({ status: 404 });
	});

	it('refuses a visitor without aml:read', async () => {
		const { fetch, calls } = api({});
		await expect(
			run(load, {
				params: { key: 'cl_1' },
				url: new URL('http://admin.test/aml/clients/cl_1'),
				locals: locals([]),
				fetch
			})
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});
});

describe('the client page acts', () => {
	it('confirms or rejects a match on the check the form names, with the comment', async () => {
		const { fetch, calls } = api({ '/decision': { ok: true } });
		const result = await run(actions.decideMatch, {
			request: form({
				check: 'chk_1',
				match: 'm 1',
				decision: 'dismissed',
				comment: 'Born 1971, not 1958'
			}),
			locals: locals(),
			fetch
		});
		expect(result).toEqual({ action: 'decideMatch', match: 'm 1', decision: 'dismissed' });
		expect(calls[0].url).toMatch(/\/aml\/checks\/chk_1\/matches\/m%201\/decision$/);
		expect(JSON.parse(String(calls[0].body))).toEqual({
			decision: 'dismissed',
			comment: 'Born 1971, not 1958'
		});
	});

	it('needs a decision, not a comment, before anything is sent', async () => {
		const { fetch, calls } = api({ '/decision': { ok: true } });
		const noComment = await run(actions.decideMatch, {
			request: form({ check: 'chk_1', match: 'm1', decision: 'confirmed', comment: '  ' }),
			locals: locals(),
			fetch
		});
		expect(noComment).toEqual({ action: 'decideMatch', match: 'm1', decision: 'confirmed' });
		expect(JSON.parse(String(calls[0].body))).toEqual({ decision: 'confirmed', comment: null });
		const noDecision = await run(actions.decideMatch, {
			request: form({ check: 'chk_1', match: 'm1', decision: 'maybe', comment: 'Hm' }),
			locals: locals(),
			fetch
		});
		expect(noDecision).toMatchObject({
			status: 400,
			data: { error: 'Choose True match or False positive.' }
		});
		const noCheck = await run(actions.decideMatch, {
			request: form({ match: 'm1', decision: 'confirmed', comment: 'PEP' }),
			locals: locals(),
			fetch
		});
		expect(noCheck).toMatchObject({ status: 400, data: { error: 'No match given.' } });
		expect(calls).toHaveLength(1);
	});

	it('allows a held journey to continue, with or without a comment', async () => {
		const { fetch, calls } = api({ '/allow': { ok: true } });
		const result = await run(actions.allowJourney, {
			request: form({ check: 'chk_1', comment: 'Domestic PEP, low risk, EDD done' }),
			locals: locals(),
			fetch
		});
		expect(result).toEqual({ action: 'allowJourney' });
		expect(calls[0].url).toMatch(/\/aml\/checks\/chk_1\/allow$/);
		expect(JSON.parse(String(calls[0].body))).toEqual({
			comment: 'Domestic PEP, low risk, EDD done'
		});
		const noComment = await run(actions.allowJourney, {
			request: form({ check: 'chk_1', comment: '' }),
			locals: locals(),
			fetch
		});
		expect(noComment).toEqual({ action: 'allowJourney' });
		expect(JSON.parse(String(calls[1].body))).toEqual({ comment: null });
		const noCheck = await run(actions.allowJourney, {
			request: form({ comment: 'x' }),
			locals: locals(),
			fetch
		});
		expect(noCheck).toMatchObject({ status: 400, data: { error: 'No check given.' } });
		expect(calls).toHaveLength(2);
	});

	it('clears an identity check on the check the form names', async () => {
		const { fetch, calls } = api({ '/aml/checks/chk_7/decision': { ok: true } });
		const result = await run(actions.decideCheck, {
			request: form({
				check: 'chk_7',
				decision: 'clear',
				reason: 'Document and selfie verified on review'
			}),
			locals: locals(),
			fetch
		});
		expect(result).toEqual({ action: 'decideCheck', decision: 'clear' });
		expect(JSON.parse(String(calls[0].body))).toEqual({
			decision: 'clear',
			comment: 'Document and selfie verified on review'
		});
	});

	it('turns monitoring on and runs a failed screen again, each on its check', async () => {
		const { fetch, calls } = api({
			'/aml/checks/chk_3/monitoring': { ok: true },
			'/aml/checks/chk_3/rerun': { check_uid: 'chk_4' }
		});
		expect(
			await run(actions.monitoring, {
				request: form({ check: 'chk_3', enabled: 'on' }),
				locals: locals(),
				fetch
			})
		).toEqual({ action: 'monitoring', enabled: true });
		expect(
			await run(actions.rerun, { request: form({ check: 'chk_3' }), locals: locals(), fetch })
		).toEqual({ action: 'rerun' });
		expect(calls.map((c) => [c.method, c.url.replace(/^.*\/aml/, '/aml')])).toEqual([
			['PUT', '/aml/checks/chk_3/monitoring'],
			['POST', '/aml/checks/chk_3/rerun']
		]);
	});

	it('adds an internal note on the client the page names, and refuses a blank one', async () => {
		const { fetch, calls } = api({
			'/notes': { uid: 'n1', body: 'EDD done', author: 'Olive', created_at: '2026-10-02' }
		});
		const blank = await run(actions.addNote, {
			request: form({ body: '  \n ' }),
			params: { key: 'c 1' },
			locals: locals(),
			fetch
		});
		expect(blank).toMatchObject({
			status: 400,
			data: { action: 'addNote', error: 'Write a note.' }
		});
		expect(calls).toHaveLength(0);

		const added = await run(actions.addNote, {
			request: form({ body: '  EDD done.\nSource of funds: house sale.  ' }),
			params: { key: 'c 1' },
			locals: locals(),
			fetch
		});
		expect(added).toEqual({ action: 'addNote' });
		expect(calls.map((c) => [c.method, c.url.replace(/^.*\/aml/, '/aml')])).toEqual([
			['POST', '/aml/clients/c%201/notes']
		]);
		expect(JSON.parse(String(calls[0].body))).toEqual({
			body: 'EDD done.\nSource of funds: house sale.'
		});
	});

	it('keeps a note behind aml:write', async () => {
		const { fetch, calls } = api({});
		await expect(
			run(actions.addNote, {
				request: form({ body: 'x' }),
				params: { key: 'c1' },
				locals: locals(['aml:read']),
				fetch
			})
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});

	it('screens a client and moves to their lasting key', async () => {
		const { fetch, calls } = api({
			'/aml/screen': { check_uid: 'chk_new' },
			'/aml/checks/chk_new/client': { key: 'cl_new', check_uid: 'chk_new' }
		});
		await expect(
			run(actions.screen, {
				request: form({ contact_uid: 'c1' }),
				locals: locals(),
				fetch
			})
		).rejects.toMatchObject({ status: 303, location: '/aml/clients/cl_new' });
		expect(JSON.parse(String(calls[0].body))).toEqual({ contact_uid: 'c1' });
	});

	it('surfaces the API refusal of a screen', async () => {
		const { fetch } = api({
			'/aml/screen': new Response(JSON.stringify({ detail: 'ComplyCube is not configured.' }), {
				status: 503
			})
		});
		const result = await run(actions.screen, {
			request: form({ contact_uid: 'c1' }),
			locals: locals(),
			fetch
		});
		expect(result).toMatchObject({
			status: 503,
			data: { action: 'screen', error: 'ComplyCube is not configured.' }
		});
	});

	it('keeps writes behind aml:write and decisions behind aml:adjudicate', async () => {
		const { fetch, calls } = api({});
		await expect(
			run(actions.screen, {
				request: form({ contact_uid: 'c1' }),
				locals: locals(['aml:read']),
				fetch
			})
		).rejects.toMatchObject({ status: 403 });
		await expect(
			run(actions.decideCheck, {
				request: form({ check: 'chk_1', decision: 'clear', reason: 'x' }),
				locals: locals(['aml:read', 'aml:write']),
				fetch
			})
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});
});

describe('an old check link', () => {
	it('opens its client page on that check', async () => {
		const { fetch, calls } = api({
			'/aml/checks/chk_1/client': { key: 'cl_1', check_uid: 'chk_1' }
		});
		await expect(
			run(checkRedirect, { params: { uid: 'chk_1' }, locals: locals(['aml:read']), fetch })
		).rejects.toMatchObject({ status: 303, location: '/aml/clients/cl_1?check=chk_1' });
		expect(calls[0].url).toMatch(/\/aml\/checks\/chk_1\/client$/);
	});

	it('is a 404 for a check the console does not know', async () => {
		const { fetch } = api({});
		await expect(
			run(checkRedirect, { params: { uid: 'nope' }, locals: locals(['aml:read']), fetch })
		).rejects.toMatchObject({ status: 404 });
	});
});
