/**
 * Every link into a TENANT route carries the firm's UID — the case that shipped the 404.
 *
 * The admin tenant section moved from `/tenants/{id}` (the serial) to `/tenants/{uid}`, and
 * `href="/tenants/{t.id}"` is valid TypeScript against a row that HAS an `id`: svelte-check,
 * eslint and every unit test passed while the firm listing linked to `/tenants/3`. Nothing in the
 * type system can notice, because nothing about the string says which field belongs in it.
 *
 * The RULE now lives in `src/route-addressing.ts` and is enforced across all three portals by
 * `src/route-addressing.test.ts`. What stays here is what is specific to this section: the tenant
 * spellings the pre-uid code used, and — below — the behavioural half no scan can express.
 */
import { describe, expect, it } from 'vitest';
import { actions } from './+page.server';
import { inner, LINK, linksIn, SERIAL_EXPRESSION, sources } from '../../route-addressing';

/** The tenant-shaped spellings, on top of the shared rule. `tenant.id` and `data.tenant.id` are
 *  the two the firm listing and the section layout used. */
const TENANT_SERIAL = /(^|[.\s{(])(tenant\.id|data\.tenant\.id)([}\s)?,]|$)/;

const tenantLinks = (): { path: string; expression: string }[] =>
	sources('admin').flatMap((path) =>
		linksIn(path)
			.filter((expression) => expression.length > 0)
			.map((expression) => ({ path, expression }))
	);

describe('links into a tenant route', () => {
	it('never interpolate a serial id — the routes are uid-addressed', () => {
		const offenders = tenantLinks()
			.filter(
				({ expression }) =>
					SERIAL_EXPRESSION.test(inner(expression)) || TENANT_SERIAL.test(inner(expression))
			)
			.map(({ path, expression }) => `${path}: ${expression}`);
		expect(offenders).toEqual([]);
	});

	it('finds the links it is meant to be scanning', () => {
		// A guard on the guard: if the glob or the pattern ever stops matching, the assertion above
		// passes vacuously and the rule silently stops being enforced.
		expect(tenantLinks().length).toBeGreaterThan(20);
	});

	it('would catch the regression it exists for', () => {
		// The exact shape that shipped a 404: the firm listing's row link.
		const bad = '<a href="/tenants/{t.id}" class="font-medium">{t.name}</a>';
		expect(SERIAL_EXPRESSION.test(inner([...bad.matchAll(LINK)][0][2]))).toBe(true);

		// …and does not flag the fixed one, nor a uid-bearing nested read.
		for (const good of [
			'"/tenants/{t.uid}"',
			'`/tenants/${tenantUid}`',
			'"/tenants/{data.tenant.uid}"'
		]) {
			const expr = inner([...good.matchAll(LINK)][0][2]);
			expect(SERIAL_EXPRESSION.test(expr) || TENANT_SERIAL.test(expr), good).toBe(false);
		}
	});
});

describe('creating a firm hands back what the link needs', () => {
	// The static scan above reads HREFS. This one is the other half: the new-firm dialog navigates
	// with `goto(\`/tenants/${result.data.uid}\`)`, and the action returned only `{ ok, id }` — so
	// the guard (`result.data?.id`) passed, the uid was `undefined`, and every newly created firm
	// landed on `/tenants/undefined`.
	//
	// A behavioural test rather than a scan, because there is no string to scan: the field was
	// simply absent from a payload, and the `id` sitting beside it made the object look complete.
	// `POST /admin/tenants` has always returned both (`RETURNING id, uid, …`).
	const locals = () =>
		({
			user: { id: 'u-ops' },
			accessToken: 'tok',
			permissions: ['tenants:write'],
			role: null
		}) as never;

	const form = () => {
		const data = new FormData();
		data.set('name', 'Acme Advisers');
		data.set('alias', 'acme');
		return { formData: async () => data } as unknown as Request;
	};

	const created = { id: 42, uid: 'ten_abc123', alias: 'acme', name: 'Acme Advisers' };

	it('returns the uid the redirect is built from', async () => {
		const fetch = (async () =>
			new Response(JSON.stringify(created), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			})) as unknown as typeof globalThis.fetch;

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const out = await (actions.createTenant as any)({ request: form(), locals: locals(), fetch });
		expect(out.uid).toBe('ten_abc123');
		// The id stays: the ~40 `/admin/tenants/{tenant_id}` endpoints keep their integer contract,
		// and `$lib/api::tenantIdFor` exists precisely because form actions cannot read layout data.
		expect(out.id).toBe(42);
	});

	it('surfaces the API refusal rather than a half-made firm', async () => {
		const fetch = (async () =>
			new Response(JSON.stringify({ detail: "the alias 'acme' is already used by another firm" }), {
				status: 409,
				headers: { 'content-type': 'application/json' }
			})) as unknown as typeof globalThis.fetch;

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const out = await (actions.createTenant as any)({ request: form(), locals: locals(), fetch });
		expect(out.status).toBe(409);
		expect(String(out.data.error)).toContain('already used');
	});
});
