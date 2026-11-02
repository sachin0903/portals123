/**
 * The root layout's "Onboarding" nav entry (`+layout.server.ts` → `loadOnboarding`).
 *
 * It used to key on the client having a step of their own in `pending`, which meant the tab —
 * their only route back to the page that explains what is happening — vanished the moment they
 * finished their terms declarations and reappeared whenever their adviser released something
 * else. Across a real application that is most of its life. It now follows the JOURNEY: any
 * unsettled milestone keeps the tab, no journey at all hides it, and a finished one hides it too.
 */

import { describe, expect, it, vi } from 'vitest';
import { load } from './+layout.server';

type Mine = {
	steps: { status: string }[];
	done: number;
	total: number;
	stages?: { status: 'done' | 'current' | 'upcoming' }[];
};

/** Every URL the layout asked for, in order — so a test can assert on the REQUEST as well as
 *  on the answer. */
const asked: string[] = [];

function stubFetch(mine: Mine | null) {
	return vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		asked.push(url);
		if (url.includes('/crm/contacts/mine')) {
			return {
				ok: true,
				json: async () => ({ contacts: [{ id: 1, name: 'Only Client' }], default_contact_id: 1 })
			} as unknown as Response;
		}
		if (url.includes('/applications/me')) {
			// `null` stands for the API refusing — the tab must hide rather than break the shell.
			if (!mine) return { ok: false, json: async () => ({}) } as unknown as Response;
			return { ok: true, json: async () => mine } as unknown as Response;
		}
		if (url.includes('/notifications')) {
			return { ok: true, json: async () => ({ items: [], count: 0 }) } as unknown as Response;
		}
		throw new Error(`Unexpected fetch: ${url}`);
	}) as unknown as typeof globalThis.fetch;
}

type LoadArg = Parameters<typeof load>[0];

const tabShown = async (mine: Mine | null): Promise<boolean> => {
	const data = (await load({
		locals: { user: { id: 'u1', email: 'jo@example.test' }, accessToken: 'tok' },
		fetch: stubFetch(mine),
		cookies: { get: vi.fn(() => undefined), set: vi.fn() },
		depends: () => {},
		url: { pathname: '/' }
	} as unknown as LoadArg)) as { onboarding: boolean };
	return data.onboarding;
};

describe('client shell — Onboarding nav entry', () => {
	it('stays while the journey runs, even with nothing pending for the client', async () => {
		// The state the old gate got wrong: terms signed, adviser now doing AML and account setup.
		expect(
			await tabShown({
				steps: [{ status: 'fulfilled' }],
				done: 1,
				total: 1,
				stages: [
					{ status: 'done' },
					{ status: 'done' },
					{ status: 'current' },
					{ status: 'upcoming' }
				]
			})
		).toBe(true);
	});

	it('shows while the client has work of their own', async () => {
		asked.length = 0;
		expect(
			await tabShown({
				steps: [{ status: 'pending' }],
				done: 0,
				total: 3,
				stages: [{ status: 'current' }, { status: 'upcoming' }]
			})
		).toBe(true);
		expect(asked.some((url) => url.includes('/notifications'))).toBe(false);
	});

	it('hides once every milestone is done', async () => {
		expect(
			await tabShown({
				steps: [{ status: 'fulfilled' }],
				done: 1,
				total: 1,
				stages: [{ status: 'done' }, { status: 'done' }]
			})
		).toBe(false);
	});

	it('hides when there is no journey at all', async () => {
		expect(await tabShown({ steps: [], done: 0, total: 0, stages: [] })).toBe(false);
	});

	it('falls back to the client’s own pending work when the API sends no stages', async () => {
		expect(await tabShown({ steps: [{ status: 'pending' }], done: 0, total: 2 })).toBe(true);
		expect(await tabShown({ steps: [{ status: 'fulfilled' }], done: 2, total: 2 })).toBe(false);
	});

	it('hides — never throws — when the onboarding read fails', async () => {
		expect(await tabShown(null)).toBe(false);
	});

	it('asks about the ACTIVE contact, not the whole login', async () => {
		// This flag does not merely add a tab: `$lib/nav.ts` reduces the whole bar to Onboarding
		// while it is true, and `+page.server.ts` redirects `/` there. Asked login-wide, a login
		// linked to two clients had one contact's live journey answer for both — switching to the
		// fully-onboarded client landed back on the other one's application, and the switcher's
		// own destination is `/`, which bounced there again. There was no way out but signing out.
		asked.length = 0;
		await tabShown({ steps: [], done: 0, total: 0, stages: [] });
		const applications = asked.find((u) => u.includes('/applications/me'));
		expect(applications).toContain('contact_id=1');
	});
});
