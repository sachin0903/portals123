import { existsSync, globSync, readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { actions as transferActions, load as loadTransfers } from './transfers/+page.server';
import { POST as createContribution } from './accounts/[accountUid]/contributions/+server';

const tenant = { id: 7, uid: 'ten_target', name: 'Target Wealth' };
const locals = {
	user: { id: 'ops', email: 'ops@example.test' },
	accessToken: 'token',
	permissions: ['tenant_portal:write'],
	role: null
};

describe('tenant daily-operation surfaces', () => {
	it('implements every advertised shared surface and its UID detail route with target-aware code', () => {
		for (const route of [
			'contributions',
			'transfers',
			'tasks',
			'tasks/[taskUid]',
			'messages',
			'journeys',
			'journeys/[journeyUid]',
			'journeys/[journeyUid]/steps/[caseUid]/[stepKey]',
			'journeys/new',
			'documents',
			'documents/[documentUid]',
			'documents/requests',
			'documents/requests/[requestUid]',
			'documents/generate',
			'documents/send',
			'documents/types',
			'clients',
			'clients/[clientUid]',
			'clients/[clientUid]/transactions',
			'clients/[clientUid]/chat',
			'clients/[clientUid]/documents',
			'clients/[clientUid]/documents/[documentUid]',
			'clients/[clientUid]/compliance',
			'clients/[clientUid]/activity',
			'clients/[clientUid]/illustrations',
			'accounts',
			'accounts/[accountUid]/transactions',
			'look-through/[fundUid]'
		]) {
			const pagePath = `src/routes/tenants/[uid]/operate/${route}/+page.svelte`;
			const serverPath = `src/routes/tenants/[uid]/operate/${route}/+page.server.ts`;
			expect(existsSync(pagePath), route).toBe(true);
			expect(existsSync(serverPath), route).toBe(true);
			const page = readFileSync(pagePath, 'utf8');
			const server = readFileSync(serverPath, 'utf8');
			expect(page, route).not.toMatch(/coming soon|not implemented|TODO:/i);
			expect(page, route).toMatch(/data\.|@graphene\/ui|<svelte:component|<form|<section/i);
			expect(server, route).toMatch(
				/\$lib\/operate|target(?:Json|Request|Mutation)|export \{ actions, load \} from/
			);
		}
	});

	it('implements all account bands and same-origin write proxies', () => {
		for (const route of [
			'accounts/[accountUid]',
			'accounts/[accountUid]/holdings',
			'accounts/[accountUid]/transactions'
		]) {
			expect(existsSync(`src/routes/tenants/[uid]/operate/${route}/+page.svelte`), route).toBe(
				true
			);
		}
		for (const proxy of [
			'contributions',
			'transfers',
			'instructions',
			'drawdown',
			'update/[kind]'
		]) {
			const source = readFileSync(
				`src/routes/tenants/[uid]/operate/accounts/[accountUid]/${proxy}/+server.ts`,
				'utf8'
			);
			expect(source, proxy).toContain('canOperateWrite');
			expect(source, proxy).toMatch(/target(?:Json|Mutation)/);
			expect(source, proxy).not.toMatch(/apiUrl|bookUrl|fixture|mock/i);
		}
		const bankProxy = readFileSync(
			'src/routes/tenants/[uid]/operate/accounts/[accountUid]/update/[kind]/+server.ts',
			'utf8'
		);
		expect(bankProxy).toContain('/crm/accounts/${account}/bank-accounts');
		expect(bankProxy).not.toContain('/crm/contacts/');
	});

	it('keeps every operate API call behind the target helper', () => {
		const servers = [
			'clients/[clientUid]/+layout.server.ts',
			'clients/[clientUid]/transactions/+page.server.ts',
			'clients/[clientUid]/documents/+page.server.ts',
			'clients/[clientUid]/compliance/+page.server.ts',
			'clients/[clientUid]/activity/+page.server.ts',
			'clients/[clientUid]/illustrations/+page.server.ts',
			'accounts/[accountUid]/+layout.server.ts',
			'accounts/[accountUid]/transactions/+page.server.ts',
			'look-through/[fundUid]/+page.server.ts',
			'journeys/new/+page.server.ts',
			'documents/requests/+page.server.ts',
			'documents/generate/+page.server.ts',
			'documents/send/+page.server.ts',
			'documents/types/+page.server.ts'
		];
		for (const route of servers) {
			const source = readFileSync(`src/routes/tenants/[uid]/operate/${route}`, 'utf8');
			expect(source, route).toContain('$lib/operate');
			expect(source, route).not.toMatch(/\bapiUrl\b|\bbookUrl\b|fixture|mock/i);
		}
	});

	it('has no unscoped API escape hatch in any direct or child server route', () => {
		for (const file of globSync('src/routes/tenants/*/operate/**/*.ts').filter(
			(path) => path.endsWith('/+server.ts') || path.endsWith('/+page.server.ts')
		)) {
			const source = readFileSync(file, 'utf8');
			expect(source, file).not.toMatch(/\bapiUrl\b|\bbookUrl\b|PUBLIC_API_URL/);
			if (!source.includes('fetch(') && !source.includes('.fetch')) continue;
			expect(source, file).toMatch(
				/\$lib\/operate|\$lib\/target-tenant|\$lib\/audit\/actions-page|\$lib\/server\/aml/
			);
			if (source.includes('$lib/audit/actions-page')) {
				expect(source, file).toContain("query.set('tenant', params.uid)");
			}
			if (source.includes('$lib/server/aml')) {
				expect(source, file).toContain('clientChecks(fetch, locals.accessToken, tenant.uid,');
			}
		}
	});

	it('uses target UIDs in links and exposes messaging routes', () => {
		const account = readFileSync('src/lib/operate-account-record.svelte', 'utf8');
		expect(account).toContain('data.account.uid');
		expect(account).toContain('/operate/clients/${uid}');
		expect(account).toContain('is_pension: data.account.is_pension');
		expect(account).toContain('scheme_administrator: data.account.scheme_administrator');
		expect(account).toContain('custodian: data.account.custodian');
		expect(existsSync('src/routes/tenants/[uid]/operate/messages/+page.svelte')).toBe(true);
	});

	it('mounts shared book components rather than admin stores or fixtures', () => {
		const surfaces = {
			contributions: 'ContributionsWorkbench',
			transfers: 'TransfersWorkbench',
			tasks: 'TasksListing',
			messages: 'MessagesInbox',
			journeys: 'JourneysListing',
			documents: 'DocumentsLibrary'
		};
		for (const [route, component] of Object.entries(surfaces)) {
			const source = readFileSync(`src/routes/tenants/[uid]/operate/${route}/+page.svelte`, 'utf8');
			expect(source).toContain(`@graphene/ui/book/${route}`);
			expect(source).toContain(component);
			expect(source).not.toMatch(/fixture|seedTransfers|TransfersStore/);
		}
	});

	it('propagates the target UID alongside listing filters', async () => {
		const fetch = vi.fn(async (input: string | URL | Request) => {
			const url = new URL(String(input));
			expect(url.searchParams.get('tenant')).toBe('ten_target');
			expect(url.searchParams.get('status')).toBe('active');
			return new Response(JSON.stringify({ items: [], total: 0, facets: {} }));
		});
		// SvelteKit's generated load type is intentionally opaque to this direct unit invocation.
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const result = await (loadTransfers as any)({
			locals,
			fetch,
			parent: async () => ({ tenant, writing: true }),
			url: new URL('http://admin.test/tenants/ten_target/operate/transfers?status=active')
		});
		expect(result.query.filters.status).toBe('active');
		const book = await result.book;
		expect(book.transfers).toEqual([]);
		expect(book.loadError).toBe(false);
		expect(fetch).toHaveBeenCalledOnce();
	});

	it('refuses direct writes in read-only mode and targets writes in acting mode', async () => {
		const fetch = vi.fn(async () => new Response(JSON.stringify({ matched: 2, ambiguous: 1 })));
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const readOnly = await (transferActions.match as any)({
			locals: { ...locals, permissions: ['tenant_portal:read'] },
			fetch,
			params: { uid: tenant.uid }
		});
		expect(readOnly.status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const write = await (transferActions.match as any)({
			locals,
			fetch,
			params: { uid: tenant.uid }
		});
		expect(write.message).toContain('2 matched');
		const calls = fetch.mock.calls as unknown as Array<[string, RequestInit]>;
		const url = new URL(String(calls[0][0]));
		expect(url.pathname).toBe('/contributions/match');
		expect(url.searchParams.get('tenant')).toBe('ten_target');
		expect(calls[0][1]).toMatchObject({ method: 'POST' });
	});

	it('resolves account UIDs and targets both legs of an account write', async () => {
		const fetch = vi
			.fn()
			.mockImplementationOnce(async (input: string | URL | Request) => {
				const url = new URL(String(input));
				expect(url.pathname).toBe('/crm/accounts/acc_public');
				expect(url.searchParams.get('tenant')).toBe(tenant.uid);
				return new Response(JSON.stringify({ id: 91 }));
			})
			.mockImplementationOnce(async (input: string | URL | Request, init?: RequestInit) => {
				const url = new URL(String(input));
				expect(url.pathname).toBe('/contributions');
				expect(url.searchParams.get('tenant')).toBe(tenant.uid);
				expect(JSON.parse(String(init?.body))).toMatchObject({ account_id: 91, amount: 500 });
				return new Response(JSON.stringify({ uid: 'contribution_uid' }));
			});
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const response = await (createContribution as any)({
			locals,
			params: { uid: tenant.uid, accountUid: 'acc_public' },
			fetch,
			request: new Request('http://admin.test', {
				method: 'POST',
				body: JSON.stringify({ amount: 500 })
			})
		});
		expect(response.status).toBe(200);
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('omits component actions from every read-only mount', () => {
		for (const route of [
			'contributions',
			'transfers',
			'tasks',
			'tasks/[taskUid]',
			'journeys/[journeyUid]',
			'documents',
			'documents/[documentUid]'
		]) {
			const source = readFileSync(`src/routes/tenants/[uid]/operate/${route}/+page.svelte`, 'utf8');
			expect(source, route).toContain('data.writing');
		}
		expect(
			readFileSync('src/routes/tenants/[uid]/operate/journeys/new/+page.svelte', 'utf8')
		).toContain('{:else if data.writing}');
		expect(
			readFileSync('src/routes/tenants/[uid]/operate/datasets/new/+page.svelte', 'utf8')
		).toContain('{#if !data.canManage}');
	});
});
