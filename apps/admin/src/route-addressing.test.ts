/**
 * A URL names a record by its UID. Enforced across all three portals, on the source.
 *
 * WHY A STATIC SCAN, AND WHY HERE. Neither half of this rule is visible to a type system:
 *
 *   · a route DIRECTORY is a filename. `routes/clients/[id]/+page.svelte` type-checks perfectly
 *     while publishing `crm.contacts.id` in every link, bookmark and pasted URL;
 *   · an href is a plain string template. `href="/clients/{c.id}"` is valid TypeScript against a
 *     row that HAS an `id` — svelte-check, eslint and every unit test pass while the link 404s.
 *     That is not hypothetical: it is exactly how the firm listing shipped pointing at
 *     `/tenants/3` (see `routes/tenants/tenant-links.test.ts`, whose rule this generalises and
 *     whose behavioural half it does not replace).
 *
 * It lives in the admin app and reads the SIBLING portals' trees because the rule is one rule.
 * Three copies would be three chances to fix a regression in one portal and leave it in the
 * others — and the failure mode is a link nobody clicks until a user does. The two "finds what it
 * is meant to be scanning" cases are the guard on the guard: if the workspace is rearranged so
 * this sees nothing, it fails loudly rather than passing vacuously.
 *
 * The rule itself is `./route-addressing.ts`, shared with the tenant test.
 */
import { describe, expect, it } from 'vitest';
import { basename, dirname } from 'node:path';
import {
	apiUidPathsIn,
	apiUidPathsInText,
	dynamicSegments,
	inner,
	isSerialApiExpression,
	LINK,
	linksIn,
	linksInText,
	OPENAPI_SPEC,
	PORTALS,
	SERIAL_EXPRESSION,
	SERIAL_SEGMENT,
	sources,
	uidCollections,
	uidCollectionsFromOpenApi
} from './route-addressing';

/**
 * Segments allowed to name an id. There are none, and that is the point.
 *
 * This held one entry: `/platforms/api/[id]`, the operations console opening one row of
 * `audit.api_log`. The reason recorded here was that the table had no uid and giving it one "would
 * add a column and a migration to make a debugging view's URL prettier" — a fair trade at the
 * time, and the wrong side of it once every other uid column had a `nanoid(12)` default to
 * inherit. `0114_api_log_uid` added the column; the exception went with it.
 *
 * An entry added back needs a reason that is not "this table has no uid" — that is a migration,
 * not an exception.
 */
// Empty, and it stays empty. The one entry that used to sit here was the api-log call
// (`/platforms/api/[id]`), allowed only because `audit.api_log` had no uid to address a row
// by; `0114_api_log_uid` gave it one. An entry added back here needs a reason that is not
// "the table has no uid" — that is a migration, not an exception.
const ALLOWED = new Set<string>();

describe('a route segment is named for what it carries', () => {
	it('never names a database key — every record route is uid-addressed', () => {
		const offenders = PORTALS.flatMap((app) =>
			dynamicSegments(app)
				.filter(({ route, name }) => SERIAL_SEGMENT.test(name) && !ALLOWED.has(route))
				.map(({ route }) => route)
		);
		expect(offenders).toEqual([]);
	});

	it('finds every portal’s route tree (a guard that scans nothing is not a guard)', () => {
		for (const app of PORTALS) {
			expect(dynamicSegments(app).length, app).toBeGreaterThan(2);
		}
	});

	it('the one allowed exception still exists, and is still the api-log call id', () => {
		// An allowlist entry that no longer matches anything is a licence nobody is using — and the
		// next `[id]` route to appear would be waved through if the path drifted onto it.
		const all = new Set(PORTALS.flatMap((app) => dynamicSegments(app).map((s) => s.route)));
		for (const allowed of ALLOWED) expect(all.has(allowed), allowed).toBe(true);
	});

	it('would catch the shapes it exists for, and passes the ones it should', () => {
		for (const bad of ['id', 'tenantId', 'contact_id']) {
			expect(SERIAL_SEGMENT.test(bad), bad).toBe(true);
		}
		for (const good of ['uid', 'clientUid', 'accountUid', 'case_uid', 'step_key', 'slug']) {
			expect(SERIAL_SEGMENT.test(good), good).toBe(false);
		}
	});
});

// Each case walks every portal's source on disk: milliseconds idle, past the 5s default when the
// pre-push hook runs the API suite alongside it — the margin `pkgs/ui`'s config gives its scans.
describe('a link into a record route', { timeout: 30_000 }, () => {
	it('never interpolates a serial id', () => {
		const offenders = PORTALS.flatMap((app) =>
			sources(app).flatMap((path) =>
				linksIn(path)
					.filter((expression) => SERIAL_EXPRESSION.test(inner(expression)))
					.map((expression) => `${app}:${basename(dirname(path))}/${basename(path)} ${expression}`)
			)
		);
		expect(offenders).toEqual([]);
	});

	it('finds the links it is meant to be scanning', () => {
		const found = PORTALS.flatMap((app) => sources(app).flatMap(linksIn));
		expect(found.length).toBeGreaterThan(20);
	});

	it('would catch the regression it exists for', () => {
		// The exact shapes that shipped: the firm listing's row link, and the client listing's.
		for (const bad of ['"/tenants/{t.id}"', '"/clients/{c.id}"', '`/accounts/${a.id}`']) {
			expect(SERIAL_EXPRESSION.test(inner([...bad.matchAll(LINK)][0][2])), bad).toBe(true);
		}
		for (const good of [
			'"/tenants/{t.uid}"',
			'`/clients/${c.uid}`',
			'"/accounts/{a.uid}"',
			'"/users/{u.user_uid}"',
			'"/tenants/{data.tenant.uid}"'
		]) {
			expect(SERIAL_EXPRESSION.test(inner([...good.matchAll(LINK)][0][2])), good).toBe(false);
		}
		// …and never reaches an API path, nor a `/users` segment nested inside one. Those are the
		// second describe's subject: `/crm/contacts/{contact_uid}` is uid-typed now, so "the API
		// keeps its integer contract" — the reason this carve-out was first written down — is only
		// still true of the nested params (`{adviser_id}`, `{user_id}`) and of `/admin/tenants`.
		for (const api of [
			'`${apiUrl()}/admin/tenants/${id}`',
			'`${apiUrl()}/crm/contacts/${contactId}`',
			'`${apiUrl()}/firm/contacts/${contactId}/users/${userId}`'
		]) {
			expect([...api.matchAll(LINK)], api).toEqual([]);
		}

		// A comment discussing the old shape is history, not a link.
		expect(linksInText('// it used to carry `?from=/clients/{id}`')).toEqual([]);
		expect(linksInText('const href = `/clients/${c.id}`;')).toHaveLength(1);
	});
});

/**
 * The same rule, one layer in: a CALL into an API path that takes a uid.
 *
 * The route scan above cannot see this — it is a fetch URL, not a link — and neither can the type
 * system, because both identifiers are on the row: `detail.contact.id` type-checks perfectly
 * against a path that wanted `detail.contact.uid`. What comes back is a 404 from the resolver,
 * which on a record SECTION reads as "this client has none of that" rather than as a defect.
 *
 * The collections themselves come from the generated OpenAPI client, not a closed list:
 * hard-coding `/crm|firm/contacts|accounts` is how catalogue writes and group-document requests
 * kept shipping a serial into a uid path. A collection the spec adds is watched on the next run.
 */
describe('a call into a uid-typed API path never sends a database key', () => {
	it('holds across all three portals', () => {
		const offenders = PORTALS.flatMap((app) =>
			sources(app).flatMap((path) =>
				apiUidPathsIn(path)
					.filter((expression) => isSerialApiExpression(expression))
					.map((expression) => `${path} → ${expression}`)
			)
		);
		expect(offenders).toEqual([]);
	});

	it('finds the calls it is meant to be scanning', () => {
		const found = PORTALS.flatMap((app) => sources(app).flatMap(apiUidPathsIn));
		expect(found.length).toBeGreaterThan(20);
	});

	it('watches every uid-typed collection in the generated client, not a closed list', () => {
		const collections = uidCollections();
		expect(collections.length).toBeGreaterThan(30);
		const needles = new Set(collections.map((c) => c.needle));
		for (const needle of [
			'/catalogue/models',
			'/catalogue/products',
			'/catalogue/fee-packages',
			'/messaging/groups',
			'/messaging/conversations',
			'/contributions',
			'/transfers',
			'/documents',
			'/flows'
		]) {
			expect(needles.has(needle), needle).toBe(true);
		}
	});

	it('the OpenAPI spec it reads still exists (a guard that scans nothing is not a guard)', () => {
		expect(uidCollectionsFromOpenApi).toBeTypeOf('function');
		expect(OPENAPI_SPEC).toContain('types.gen.ts');
		expect(uidCollections().length).toBeGreaterThan(30);
	});

	it('would catch the regression it exists for', () => {
		for (const bad of [
			'`${apiUrl()}/crm/contacts/${detail.contact.id}/screening`',
			'`${apiUrl()}/crm/contacts/${candidate.id}`',
			'`${apiUrl()}/crm/contacts/${activeContactId}`',
			'bookUrl(`/firm/contacts/${contactId}/instructions`, params.uid)',
			'bookUrl(`/crm/contacts/${contactId}/bank-account`, params.uid)',
			'`${apiUrl()}/crm/accounts/${accountId}/model`',
			'`${apiUrl()}/firm/contacts/${id}/restore`',
			'`${apiUrl()}/firm/contacts/${contact_id}/portal-invite`',
			'`${apiUrl()}/admin/tenants/${t}/catalogue/models/${id}`',
			'`${apiUrl()}/messaging/groups/${groupId}/requests`',
			'`${apiUrl()}/admin/tenants/${t}/catalogue/providers/${kind}/${id}`',
			'`${apiUrl()}/flows/dead-letters/${kind}/${rowId}/replay`',
			'`${apiUrl()}/crm/contacts/${encodeURIComponent(id)}`',
			'`${apiUrl()}/crm/contacts/${encodeURIComponent(contactId)}`'
		]) {
			expect(
				apiUidPathsInText(bad).some((expression) => isSerialApiExpression(expression)),
				bad
			).toBe(true);
		}
		expect(
			apiUidPathsInText(
				'`${apiUrl()}/admin/tenants/${t}/catalogue/providers/${kind}/${id}`'
			).includes('${kind}')
		).toBe(false);
		expect(
			apiUidPathsInText(
				'const base = `${apiUrl()}/admin/tenants/${t}/catalogue/models`;\n`${base}/${id}`'
			).some((expression) => isSerialApiExpression(expression))
		).toBe(true);
		expect(
			apiUidPathsInText(
				'const base = `${apiUrl()}/admin/tenants/${t}/catalogue/providers/${kind}`;\n`${base}/${id}`'
			).some((expression) => isSerialApiExpression(expression))
		).toBe(true);
		expect(
			apiUidPathsInText(
				'const base = `${apiUrl()}/admin/tenants/${t}/catalogue/models/${m}/commentary`;\n`${base}/${id}`'
			).some((expression) => isSerialApiExpression(expression))
		).toBe(true);
		expect(isSerialApiExpression("${str(data, 'id')}")).toBe(true);
		expect(isSerialApiExpression('${encodeURIComponent(id)}')).toBe(true);
		expect(isSerialApiExpression('${encodeURIComponent(contactId)}')).toBe(true);
		expect(isSerialApiExpression('${encodeURIComponent(params.uid)}')).toBe(false);
		for (const good of [
			'`${apiUrl()}/crm/contacts/${params.uid}/screening`',
			'`${apiUrl()}/crm/contacts/${candidate.uid}`',
			'`${apiUrl()}/crm/contacts/${activeContactUid}`',
			'bookUrl(`/firm/contacts/${params.clientUid}/instructions`, params.uid)',
			'`${apiUrl()}/crm/accounts/${accountUid}/model`',
			'`${apiUrl()}/crm/contacts/${record.contact.uid}/illustrations`',
			'`${apiUrl()}/crm/accounts/${account.client.uid}`',
			'`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/models/${uid}`',
			'`${apiUrl()}/messaging/groups/${groupUid}/requests`',
			'`${apiUrl()}/flows/${flowUid}/simulate`',
			'`${apiUrl()}/admin/tenants/${tenantUid}/catalogue/providers/${kind}/${uid}`',
			'`${apiUrl()}/flows/dead-letters/${kind}/${uid}/replay`',
			'`${apiUrl()}/crm/contacts/${encodeURIComponent(params.uid)}`'
		]) {
			const [expression] = apiUidPathsInText(good);
			expect(expression, good).toBeDefined();
			expect(isSerialApiExpression(expression), good).toBe(false);
		}
		expect(apiUidPathsInText('const href = `/documents/${id}`;')).toEqual([]);
		expect(apiUidPathsInText('const href = `/flows/${data.flowId}`;')).toEqual([]);
		expect(
			apiUidPathsInText(
				"import { apiUrl } from '$lib/api';\nconst path = `/notifications/${id}/read`;\nfetch(`${apiUrl()}${path}`)"
			).some((expression) => isSerialApiExpression(expression))
		).toBe(true);
		expect(
			apiUidPathsInText(
				"import { apiUrl } from '$lib/api';\nconst path = `/contributions/${uid}/cancel`;\nfetch(`${apiUrl()}${path}`)"
			).some((expression) => isSerialApiExpression(expression))
		).toBe(false);
		expect(
			apiUidPathsInText('`${apiUrl()}/firm/contacts/${params.uid}/advisers/${adviserId}`')
		).toEqual(['${params.uid}']);
		expect(
			apiUidPathsInText("bookUrl('/crm/holdings', t, { contact_id: String(contactId) })")
		).toEqual([]);
		expect(apiUidPathsInText('// it used to be `/crm/contacts/${contactId}`')).toEqual([]);
	});
});
