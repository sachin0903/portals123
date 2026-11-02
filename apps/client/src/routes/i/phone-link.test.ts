/**
 * Opening the link spends the code. The token only ever reaches a cookie, and a code that
 * cannot be spent renders the finished page rather than a sign-in.
 */

import { describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));
vi.mock('$lib/server/workos', () => ({
	IDV_PHONE_COOKIE: 'gp-idv-phone-client',
	cookieOptions: { path: '/', httpOnly: true, sameSite: 'lax', secure: false }
}));

const redeem = vi.fn();
vi.mock('$lib/server/identity-phone', () => ({
	redeemPhoneLink: (...args: unknown[]) => redeem(...args)
}));

const { load } = await import('./[code]/+page.server');

type Cookie = { name: string; value: string; opts: Record<string, unknown> };

function cookieJar() {
	const set: Cookie[] = [];
	return {
		set,
		api: {
			get: () => undefined,
			set: (name: string, value: string, opts: Record<string, unknown>) =>
				set.push({ name, value, opts }),
			delete: () => {}
		}
	};
}

async function caught(run: () => unknown): Promise<{ status: number; location: string }> {
	try {
		await run();
	} catch (err) {
		const redirect = err as { status: number; location: string };
		if (typeof redirect?.status === 'number') return redirect;
		throw err;
	}
	throw new Error('expected a redirect');
}

describe('opening the phone link', () => {
	it('puts the session token in a cookie and leaves the code out of the address', async () => {
		redeem.mockResolvedValueOnce({ token: 'idvphone_secret', secondsRemaining: 1700 });
		const jar = cookieJar();

		const redirect = await caught(() =>
			load({ params: { code: 'once' }, cookies: jar.api, fetch: vi.fn() } as never)
		);

		expect(redirect).toMatchObject({ status: 303, location: '/i' });
		expect(jar.set).toEqual([
			{
				name: 'gp-idv-phone-client',
				value: 'idvphone_secret',
				opts: expect.objectContaining({
					path: '/i',
					httpOnly: true,
					maxAge: 1700
				})
			}
		]);
		expect(redeem).toHaveBeenCalledWith('once', {
			internalToken: 'internal-token',
			fetch: expect.any(Function)
		});
	});

	it('shows the finished page when the code cannot be spent, and sets no cookie', async () => {
		redeem.mockResolvedValueOnce(null);
		const jar = cookieJar();

		const page = await load({
			params: { code: 'spent' },
			cookies: jar.api,
			fetch: vi.fn()
		} as never);

		expect(page).toEqual({ ended: true });
		expect(jar.set).toEqual([]);
	});
});
