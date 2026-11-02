/**
 * The book section's gate + write posture.
 *
 * The gate: the whole `/tenants/[id]/book` tree requires `book:read` — an operator holding only
 * `tenants:read` can administer the firm but must not read its clients, so the layout load errors
 * cleanly (403) on a direct URL, mirroring the API's own `book_session` refusal. The nav entries
 * are gated by the same `can` call in the root layout, so the section is hidden AND locked.
 *
 * The posture: the client RECORD (and only the client record) hosts the approved middle-office
 * actions, and only for an operator holding `book:write` — every other book surface stays
 * read-only in SOURCE (`actions={null}`, no form actions, no `$app/forms`), pinned as a static
 * scan so a future edit that wires a mutation anywhere else has to delete a test that says why
 * not.
 */
import { globSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { load as gate } from './+layout.server';
import { load as index } from './+page.server';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };

type GateLocals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};
const locals = (permissions: string[], role: string | null = null): GateLocals => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role
});
const parent = async () => ({ tenant: TENANT, canManage: false });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const runGate = (args: { locals: GateLocals; parent: typeof parent }) => (gate as any)(args);

describe('the book layout gate', () => {
	it('refuses an operator without book:read, even one who can administer the tenant', async () => {
		await expect(
			runGate({ locals: locals(['tenants:read', 'tenants:write']), parent })
		).rejects.toMatchObject({ status: 403 });
	});

	it('admits book:read, and hands the tenant down to the book loaders', async () => {
		const result = await runGate({ locals: locals(['book:read']), parent });
		expect(result.tenant).toEqual(TENANT);
	});

	it('reports read-only mode for book:read alone — the banner and record key off `writing`', async () => {
		const result = await runGate({ locals: locals(['book:read']), parent });
		expect(result.writing).toBe(false);
	});

	it('reports write mode for book:write (which implies the read the gate wants)', async () => {
		const result = await runGate({ locals: locals(['book:write']), parent });
		expect(result.tenant).toEqual(TENANT);
		expect(result.writing).toBe(true);
	});

	it('admits the system PERMISSION (the only superuser signal — role names are not trusted)', async () => {
		const result = await runGate({ locals: locals(['system']), parent });
		expect(result.tenant).toEqual(TENANT);
	});

	it('does NOT trust a role merely NAMED system — the bypass is the permission', async () => {
		await expect(runGate({ locals: locals([], 'system'), parent })).rejects.toMatchObject({
			status: 403
		});
	});

	it('sends a signed-out visitor to /login before any permission talk', async () => {
		await expect(
			runGate({ locals: { ...locals(['book:read']), user: null }, parent })
		).rejects.toMatchObject({ status: 303, location: '/login' });
	});
});

describe('the book index', () => {
	it('redirects the bare /book URL to the clients listing', async () => {
		// The load is synchronous, so lift the throw into a rejection to assert on its fields.
		await expect(
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			Promise.resolve().then(() => (index as any)({ params: { uid: 'ten_4' } }))
		).rejects.toMatchObject({ status: 307, location: '/tenants/ten_4/book/clients' });
	});
});

describe('the book’s write surface is the client record, and nothing else', () => {
	// `*` rather than the literal `[uid]`: brackets are glob character classes, so the literal
	// directory name would match nothing — exactly the failure the count guard below exists for.
	// Normalise to forward slashes: globSync returns backslash paths on Windows, which would make the
	// `/`-keyed isRecord() checks miss and misclassify the client record itself as an offender.
	const norm = (paths: string[]): string[] => paths.map((p) => p.replaceAll('\\', '/'));
	const servers = norm(globSync('src/routes/tenants/*/book/**/+page.server.ts'));
	const pages = norm(globSync('src/routes/tenants/*/book/**/+page.svelte'));
	const isRecord = (f: string) => f.endsWith('/book/clients/[clientUid]/+page.server.ts');
	const isRecordPage = (f: string) => f.endsWith('/book/clients/[clientUid]/+page.svelte');

	it('scans real files (a guard that matches nothing is not a guard)', () => {
		expect(servers.length).toBeGreaterThanOrEqual(5);
		expect(pages.length).toBeGreaterThanOrEqual(5);
		expect(servers.some(isRecord)).toBe(true);
		expect(pages.some(isRecordPage)).toBe(true);
	});

	it('the client record is the ONLY book loader module exporting form actions', () => {
		const offenders = servers.filter(
			(f) => /export\s+const\s+actions/.test(readFileSync(f, 'utf8')) && !isRecord(f)
		);
		expect(offenders).toEqual([]);
		// And the record really does export them — the approved middle-office actions live there.
		expect(/export\s+const\s+actions/.test(readFileSync(servers.find(isRecord)!, 'utf8'))).toBe(
			true
		);
	});

	it('the client record page is the ONLY one importing $app/forms (enhance is the mutation path)', () => {
		const offenders = pages.filter(
			(f) => readFileSync(f, 'utf8').includes('$app/forms') && !isRecordPage(f)
		);
		expect(offenders).toEqual([]);
	});

	it('the listing stays actions={null}; the record mounts actions ONLY behind book:write', () => {
		const clientPages = pages.filter((p) => p.includes('/clients/'));
		expect(clientPages.length).toBe(2); // the listing and the record
		const listing = clientPages.find((p) => !isRecordPage(p))!;
		expect(readFileSync(listing, 'utf8')).toContain('actions={null}');
		// The record's actions are a ternary off the loader's `canWrite` (which is
		// can(..., 'book:write')) with null as the read-only arm — never unconditional.
		const record = readFileSync(clientPages.find(isRecordPage)!, 'utf8');
		expect(record).toMatch(/actions=\{data\.canWrite\s*\?/);
		expect(record).toContain(': null}');
		expect(record).not.toContain('actions={{');
	});

	it('every loader that fetches the write-side extras gates them on book:write', () => {
		// The instructions/models reads exist to feed the dialogs; a read-only operator's loader
		// must not make them at all (absence of data, not a 403, is the read-only contract).
		const record = readFileSync(servers.find(isRecord)!, 'utf8');
		expect(record).toContain("can(locals.permissions, locals.role, 'book:write')");
	});
});
