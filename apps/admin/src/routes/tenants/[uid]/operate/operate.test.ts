import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { load } from './+layout.server';

const tenant = { id: 4, uid: 'ten_acme', name: 'Acme Wealth' };
const parent = async () => ({ tenant, canManage: false });
const locals = (permissions: string[], role: string | null = null) => ({
	user: { id: 'ops_1', email: 'ops@graphene.test' },
	accessToken: 'token',
	permissions,
	role
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const run = (value: unknown) => (load as any)(value);

describe('tenant Operate shell', () => {
	it('requires tenant_portal:read independently of tenants:read', async () => {
		await expect(run({ locals: locals(['tenants:read']), parent })).rejects.toMatchObject({
			status: 403
		});
		const result = await run({ locals: locals(['tenant_portal:read']), parent });
		expect(result).toMatchObject({ tenant, writing: false });
	});

	it('uses tenant_portal:write for acting-on-behalf mode', async () => {
		const result = await run({ locals: locals(['tenant_portal:write']), parent });
		expect(result.writing).toBe(true);
	});

	it('preserves the existing domain distinctions in its capability index', async () => {
		const result = await run({
			locals: locals(['tenant_portal:read', 'book:read', 'audit:read', 'platforms:read']),
			parent
		});
		expect(result).toMatchObject({ canBook: true, canAudit: true, canPlatforms: true });
		const narrow = await run({ locals: locals(['tenant_portal:read']), parent });
		expect(narrow).toMatchObject({ canBook: false, canAudit: false, canPlatforms: false });
	});

	it('never trusts a role name and redirects signed-out visitors', async () => {
		await expect(run({ locals: locals([], 'system'), parent })).rejects.toMatchObject({
			status: 403
		});
		await expect(
			run({ locals: { ...locals(['tenant_portal:read']), user: null }, parent })
		).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('is the platform dashboard, mounted — not a second one', () => {
		// It used to be an admin-only hub (its own KPIs, a card per nav group), so staff learned a
		// dashboard the firm never sees. Both portals now mount the one `FirmDashboard`.
		const admin = readFileSync('src/routes/tenants/[uid]/operate/+page.svelte', 'utf8');
		const platform = readFileSync('../platform/src/routes/+page.svelte', 'utf8');
		for (const source of [admin, platform]) {
			expect(source).toContain("from '@graphene/ui/book/dashboard'");
			expect(source).toContain('<FirmDashboard');
		}
	});

	it('states the acting-on-behalf mode in the top bar, not above the page', () => {
		const shell = readFileSync('src/routes/+layout.svelte', 'utf8');
		expect(shell).toContain('Acting on behalf of {operateMode.name}');
		expect(shell).toContain("Viewing {operateMode.name}'s portal (read-only)");
		expect(shell).toContain('{notice}');
		const page = readFileSync('src/routes/tenants/[uid]/operate/+layout.svelte', 'utf8');
		expect(page).not.toContain('<Alert');
	});
});
