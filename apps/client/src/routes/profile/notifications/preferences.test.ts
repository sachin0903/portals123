/**
 * The preferences editor renders every category "off" when it is given nothing, and a client who
 * then presses Save writes those blanks over preferences they never chose to change. So a failed
 * read is a `loadError` the page shows a banner for — never an empty list.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadArg = Parameters<typeof load>[0];

describe('client notification preferences load', () => {
	it('returns the preferences on a clean answer', async () => {
		const prefs = [{ category: 'documents', trigger_key: 'x', in_app: true, email: false }];
		const fetch = vi.fn(async () => new Response(JSON.stringify({ preferences: prefs })));
		const result = await load({
			locals: { accessToken: 'tok' },
			fetch,
			depends: () => {}
		} as unknown as LoadArg);
		expect(result).toEqual({ preferences: prefs, loadError: false });
	});

	it('reports a loadError, not an empty editor, when the API refuses', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const fetch = vi.fn(async () => new Response('no', { status: 403 }));
		const result = await load({
			locals: { accessToken: 'tok' },
			fetch,
			depends: () => {}
		} as unknown as LoadArg);
		expect(result).toEqual({ preferences: [], loadError: true });
	});
});
