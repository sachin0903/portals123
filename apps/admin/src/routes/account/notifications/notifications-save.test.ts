/** The API skips a preference row without `trigger_key`, yet still answers 200. */
import { readFileSync } from 'node:fs';
import { expect, it, vi } from 'vitest';
import { POST } from './save/+server';

it('builds the save payload with trigger_key, because the API matches a row on it', () => {
	// The payload literal only — a `trigger_key` mentioned in a comment is not a sent field.
	const page = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
	expect(page.split('preferences: prefs.map')[1]).toMatch(/trigger_key:\s*p\.trigger_key/);
});

const authed = { user: { id: 'u-ops' }, accessToken: 'tok' };
const run = (body: unknown, fetch: typeof globalThis.fetch, locals: unknown = authed) =>
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(POST as any)({
		locals,
		fetch,
		request: new Request('http://admin.test/account/notifications/save', {
			method: 'POST',
			body: JSON.stringify(body)
		})
	});

it('forwards trigger_key to the API untouched', async () => {
	const fetch = vi.fn(
		async () => new Response(JSON.stringify({ preferences: [] }))
	) as unknown as typeof globalThis.fetch;
	const preferences = [{ category: 'tasks', trigger_key: 'task.assigned', in_app: true }];
	await run({ preferences }, fetch);

	expect(fetch).toHaveBeenCalledWith(
		expect.stringMatching(/\/notifications\/preferences$/),
		expect.objectContaining({ body: JSON.stringify({ preferences }) })
	);
});

it('refuses an anonymous caller before reaching the API', async () => {
	const fetch = (() => {
		throw new Error('must not call the API');
	}) as unknown as typeof globalThis.fetch;
	expect((await run({}, fetch, {})).status).toBe(401);
});
