/**
 * The form detail's response plane (the six splits — docs/api.md): responses are CLIENT data, so
 * reading them takes `forms:submit` (the response plane) or `documents:read` (the client-paperwork
 * readers) — `forms:read` alone views the DEFINITION only, and the loader must not even make the
 * responses fetch for it. Recording a standalone response takes `forms:submit`, not `forms:write`.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions, load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const apiForm = { uid: 'frm_1', name: 'Fact-find', description: null, status: 'published' };

function harness() {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		if (url.includes('/fields')) return json([]);
		if (url.includes('/responses')) return json([{ uid: 'res_1', answers: { a: 1 } }]);
		return json(apiForm);
	});
	return { fetch, calls };
}

const event = (permissions: string[], extra: Record<string, unknown> = {}) => ({
	params: { uid: 'frm_1' },
	locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
	...extra
});

type LoadArg = Parameters<typeof load>[0];
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const runLoad = async (e: unknown): Promise<LoadData> => (await load(e as LoadArg)) as LoadData;

describe('form detail — the response plane gates', () => {
	it('forms:read alone gets the definition and NO responses fetch', async () => {
		const { fetch, calls } = harness();
		const data = await runLoad(event(['forms:read'], { fetch }));
		expect(data.form).not.toBeNull();
		expect(data.canReadResponses).toBe(false);
		expect(data.canSubmit).toBe(false);
		expect(data.responses).toEqual([]);
		expect(calls.some((u) => u.includes('/responses'))).toBe(false);
	});

	it('forms:write alone is still definition-only — authoring is not the response plane', async () => {
		// The narrowed-role posture: the parent slug (forms:write ⇒ forms:read) keeps the builder,
		// not the client submissions.
		const { fetch, calls } = harness();
		const data = await runLoad(event(['forms:write'], { fetch }));
		expect(data.canManage).toBe(true);
		expect(data.canReadResponses).toBe(false);
		expect(calls.some((u) => u.includes('/responses'))).toBe(false);
	});

	it('forms:submit or documents:read reads the responses', async () => {
		for (const held of [
			['forms:read', 'forms:submit'],
			['forms:read', 'documents:read']
		]) {
			const { fetch, calls } = harness();
			const data = await runLoad(event(held, { fetch }));
			expect(data.canReadResponses, held.join()).toBe(true);
			expect(data.responses, held.join()).toHaveLength(1);
			expect(
				calls.some((u) => u.includes('/responses')),
				held.join()
			).toBe(true);
		}
	});
});

describe('form detail — ?/submitResponse', () => {
	const submitEvent = (permissions: string[]) => {
		const body = new FormData();
		body.append('answers', JSON.stringify({ a: 1 }));
		const calls: string[] = [];
		const fetch = vi.fn(async (input: unknown) => {
			calls.push(String(input));
			return json({ uid: 'res_2' }, 201);
		});
		return {
			calls,
			event: {
				params: { uid: 'frm_1' },
				request: new Request('http://portal.test/forms/frm_1?/submitResponse', {
					method: 'POST',
					body
				}),
				locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
				fetch
			}
		};
	};
	type ActionArg = Parameters<typeof actions.submitResponse>[0];

	it('refuses forms:write alone — authoring no longer records responses', async () => {
		const { event: e, calls } = submitEvent(['forms:write']);
		const result = await actions.submitResponse(e as unknown as ActionArg);
		expect(result).toMatchObject({ status: 403 });
		expect(calls).toEqual([]);
	});

	it('records for forms:submit', async () => {
		const { event: e, calls } = submitEvent(['forms:submit']);
		const result = await actions.submitResponse(e as unknown as ActionArg);
		expect(result).toMatchObject({ ok: true });
		expect(calls).toHaveLength(1);
	});
});
