/**
 * The estate Money rows used to render for any signed-in operator. The loaders now refuse
 * the same way the rest of the sidebar does, on the permissions the platform workbenches use.
 */
import { describe, expect, it } from 'vitest';
import { load as loadContributions } from './contributions/+page.server';
import { load as loadTransfers } from './transfers/+page.server';
import { load as loadDashboard } from './+page.server';

type Locals = {
	user: { id: string } | null;
	permissions: string[];
	role: string | null;
	accessToken: string;
};

function event(permissions: string[], user: Locals['user'] = { id: 'ops_1' }) {
	return {
		locals: { user, permissions, role: null, accessToken: 'tok' } satisfies Locals,
		fetch: () => {
			throw new Error('the gate must refuse before any fetch');
		}
	};
}

describe('the Money workbench gates', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		await expect(loadContributions(event([], null) as never)).rejects.toMatchObject({
			status: 303,
			location: '/login'
		});
		await expect(loadTransfers(event([], null) as never)).rejects.toMatchObject({
			status: 303,
			location: '/login'
		});
	});

	it('403s without the read permission', async () => {
		await expect(loadContributions(event(['transfers:read']) as never)).rejects.toMatchObject({
			status: 403
		});
		await expect(loadTransfers(event(['contributions:read']) as never)).rejects.toMatchObject({
			status: 403
		});
	});

	it('admits the matching read permission', async () => {
		await expect(loadContributions(event(['contributions:read']) as never)).resolves.toEqual({});
		await expect(loadTransfers(event(['transfers:read']) as never)).resolves.toEqual({});
	});

	it('sends a money-only operator to the workbench they can open', async () => {
		await expect(loadDashboard(event(['contributions:read']) as never)).rejects.toMatchObject({
			status: 303,
			location: '/contributions'
		});
		await expect(loadDashboard(event(['transfers:read']) as never)).rejects.toMatchObject({
			status: 303,
			location: '/transfers'
		});
	});
});
