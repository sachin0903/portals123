/**
 * The "a URL names a record by its UID" rule, as data.
 *
 * NODE-ONLY, and imported by tests alone (`route-addressing.test.ts` enforces it across all three
 * portals; `routes/tenants/tenant-links.test.ts` keeps the tenant-specific cases that shipped the
 * regression this exists for). It reads the filesystem, so it deliberately does NOT live under
 * `$lib` — nothing in the app bundle should be able to reach it.
 *
 * One source because the rule is one rule: two copies of these regexes is two chances to loosen
 * one and leave the other, on a class of bug no type system can see.
 */
import { globSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** The portals, relative to `apps/admin` (where the scan runs). */
export const PORTALS = ['client', 'platform', 'admin'] as const;
export const portalRoot = (app: string): string => `../${app}/src`;

/**
 * A dynamic route segment whose NAME says "database key": `[id]`, `[tenantId]`, `[contact_id]`.
 *
 * Deliberately name-shaped rather than value-shaped — the point is that a reader, and the next
 * person to add a route, can tell from the directory alone. `[uid]`, `[clientUid]`, `[case_uid]`
 * and `[step_key]` all pass; so does a non-identifier segment like `[slug]` or `[kind]`.
 */
export const SERIAL_SEGMENT = /^(id|.*_id|.*Id)$/;

/**
 * An in-app link into one of the uid-addressed record routes.
 *
 * The lookbehind requires the path to START here — after a quote, a backtick or an opening paren
 * — so it matches an href and not a SEGMENT of a longer API path. Without it,
 * `/firm/contacts/${contactId}/users/${userId}` reads as a link into `/users`; with it, the API's
 * own id-typed paths (`/admin/tenants/${id}`, `/crm/contacts/…`) are simply never reached, and
 * they keep their integer contract for good reason. Hrefs, redirects and gotos are the subject.
 */
export const LINK = /(?<=["'`(])\/(tenants|clients|accounts|users)\/(\{[^}]+\}|\$\{[^}]+\})/g;

/**
 * An interpolation that reads a database key rather than a public handle.
 *
 * Narrow on purpose: it matches the `id` spellings the pre-uid code used and leaves anything
 * ending `uid`/`Uid` alone, so `${t.uid}`, `${clientUid}` and `${data.tenant.uid}` all pass.
 */
export const SERIAL_EXPRESSION =
	/(^|[.\s{(])(id|tenant_id|tenantId|contact_id|contactId|account_id|accountId|user_id|userId)([}\s)?,]|$)/;

/**
 * Generated OpenAPI URLs. The API half of this rule used to hard-code `/crm|firm/contacts|accounts`,
 * which is exactly how catalogue writes and group-document requests kept shipping a serial into a
 * uid path: nothing was watching those collections. The spec is the list of uid-typed paths; a
 * hand-maintained subset is how the next one is missed.
 *
 * Resolved from `apps/admin` (vitest cwd), the same convention `portalRoot` uses.
 */
export const OPENAPI_SPEC = join('..', '..', 'pkgs', 'api-client', 'src', 'types.gen.ts');

export type UidCollection = { needle: string; param: string };

/** Path prefixes that never appear as a portal href, so a match is an API call even without
 *  `apiUrl()` in the same template (`/crm/contacts/${id}` in a test fixture, `/catalogue/models`). */
const API_ONLY_HEAD = new Set(['crm', 'firm', 'admin', 'messaging', 'aml', 'catalogue', 'storage']);

const isUidParam = (param: string): boolean => param === 'uid' || param.endsWith('_uid');

const openApiUrls = (spec: string): string[] => [
	...new Set([...spec.matchAll(/url: "([^"]+)"/g)].map((m) => m[1]))
];

const paramName = (segment: string): string | undefined => segment.match(/^\{([a-z_]+)\}$/)?.[1];

/**
 * Every uid-typed collection in the generated client: the static path immediately before a
 * `{uid}` / `{*_uid}` param (up to two segments, so `/catalogue/models` is not collapsed into
 * `/models` and `/documents/requests` is not collapsed into `/documents`).
 *
 * Used by the "the spec is still being read" assertion; matching itself walks the full path
 * (see `uidSlotPatternsFromOpenApi`) so a `{kind}` sitting between the collection and the uid
 * cannot hide the serial.
 */
export const uidCollectionsFromOpenApi = (spec: string): UidCollection[] => {
	const seen = new Set<string>();
	const out: UidCollection[] = [];
	for (const url of openApiUrls(spec)) {
		const parts = url.split('/').filter(Boolean);
		for (let i = 0; i < parts.length; i += 1) {
			const param = paramName(parts[i]);
			if (!param || !isUidParam(param)) continue;
			const staticSegs: string[] = [];
			for (let j = i - 1; j >= 0 && staticSegs.length < 2; j -= 1) {
				if (paramName(parts[j])) continue;
				staticSegs.unshift(parts[j]);
			}
			if (!staticSegs.length) continue;
			const needle = `/${staticSegs.join('/')}`;
			const key = `${needle}\0${param}`;
			if (seen.has(key)) continue;
			seen.add(key);
			out.push({ needle, param });
		}
	}
	return out;
};

/** One OpenAPI path turned into a template matcher that captures only the uid-typed slots.
 *
 * `/catalogue/providers/{kind}/{provider_uid}` must capture the SECOND interpolation, not
 * `{kind}`: a needle that stops at `/catalogue/providers` is how `${kind}/${id}` shipped
 * unwatched. Non-uid params (`{kind}`, `{provider}`, `{vid}`, `{user_id}`) are consumed and
 * not captured.
 */
export type UidSlotPattern = { source: string; firstSeg: string };

const escapeRe = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export const uidSlotPatternsFromOpenApi = (spec: string): UidSlotPattern[] => {
	const seen = new Set<string>();
	const out: UidSlotPattern[] = [];
	for (const url of openApiUrls(spec)) {
		const parts = url.split('/').filter(Boolean);
		if (!parts.length) continue;
		let source = '';
		let uidSlots = 0;
		for (const part of parts) {
			const param = paramName(part);
			if (!param) {
				source += `/${escapeRe(part)}`;
				continue;
			}
			if (isUidParam(param)) {
				source += `/(\\$\\{[^}]+\\})`;
				uidSlots += 1;
			} else {
				source += `/\\$\\{[^}]+\\}`;
			}
		}
		if (!uidSlots || seen.has(source)) continue;
		seen.add(source);
		out.push({ source, firstSeg: parts[0] });
	}
	out.sort((a, b) => b.source.length - a.source.length);
	return out;
};

/**
 * Prefix of a uid-typed path with the LAST uid segment dropped — the `const base = \`…\``
 * half of `${base}/${uid}`. Matches `/catalogue/models` and `/catalogue/providers/${kind}`
 * and `/catalogue/models/${modelUid}/commentary`, which a "ends with the collection needle"
 * test cannot see.
 */
export const uidCollectionRootsFromOpenApi = (spec: string): string[] => {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const url of openApiUrls(spec)) {
		const parts = url.split('/').filter(Boolean);
		const last = paramName(parts[parts.length - 1] ?? '');
		if (!last || !isUidParam(last)) continue;
		let source = '';
		for (const part of parts.slice(0, -1)) {
			const param = paramName(part);
			source += param ? `/\\$\\{[^}]+\\}` : `/${escapeRe(part)}`;
		}
		if (!source || seen.has(source)) continue;
		seen.add(source);
		out.push(source);
	}
	return out;
};

let cachedSpec: string | undefined;
const openApiSpec = (): string => (cachedSpec ??= readFileSync(OPENAPI_SPEC, 'utf8'));

let cachedCollections: UidCollection[] | undefined;
export const uidCollections = (): UidCollection[] => {
	cachedCollections ??= uidCollectionsFromOpenApi(openApiSpec());
	return cachedCollections;
};

let cachedSlots: UidSlotPattern[] | undefined;
const uidSlotPatterns = (): UidSlotPattern[] => {
	cachedSlots ??= uidSlotPatternsFromOpenApi(openApiSpec());
	return cachedSlots;
};

let cachedRoots: string[] | undefined;
const uidCollectionRoots = (): string[] => {
	cachedRoots ??= uidCollectionRootsFromOpenApi(openApiSpec());
	return cachedRoots;
};

/**
 * An interpolation that reads a database key, for the API rule.
 *
 * Separate from `SERIAL_EXPRESSION` because that one is a fixed list of the spellings the pre-uid
 * hrefs used, and it does not match `${activeContactId}` — one of the shapes that actually shipped
 * broken. This asks the shape instead: the LAST segment of the expression is exactly `id`, or ends
 * `_id` / `Id`. `uid`, `clientUid` and `subject_uid` pass, and so does a name like `cid` that merely
 * ends in those two letters.
 */
export const SERIAL_API_EXPRESSION = /^id$|_id$|Id$/;

/** The interpolated expression inside a matched link, without its braces. */
export const inner = (expression: string): string =>
	expression.replace(/^\$?\{/, '').replace(/\}$/, '');

const WRAP_CALL = /^(?:encodeURIComponent|String|Number)\((.*)\)\s*$/;

/** The last dotted segment of an interpolated expression — what its name actually says.
 *
 * Unwraps `encodeURIComponent(id)` / `String(saved.uid)` first: those wrappers are how look-through
 * and notification-dismiss spell a uid, and leaving them on made `${encodeURIComponent(id)}` look
 * like a function name rather than a serial. */
export const lastSegment = (expression: string): string => {
	let body = inner(expression).trim();
	while (WRAP_CALL.test(body)) body = body.replace(WRAP_CALL, '$1').trim();
	return body
		.replace(/\[[^\]]*\]/g, '')
		.split('.')
		.pop()!
		.trim();
};

/**
 * True when the interpolation is named for a database key.
 *
 * `SERIAL_API_EXPRESSION` covers `id`, `contactId`, `flowId`, `groupId`. The extra clauses catch
 * the catalogue shape the last-segment test cannot see: `str(data, 'id')` and
 * `str(data, 'model_id')` — lastSegment of those is the whole call, which matches neither `^id$`
 * nor `_id$`.
 */
export const isSerialApiExpression = (expression: string): boolean => {
	const body = inner(expression).trim();
	if (SERIAL_API_EXPRESSION.test(lastSegment(expression))) return true;
	if (/(['"])id\1/.test(body)) return true;
	if (/(['"])[a-z]+_id\1/.test(body) && !/(['"])[a-z_]*uid\1/.test(body)) return true;
	return false;
};

/**
 * Comments, stripped before scanning.
 *
 * A comment is where a change like this one gets EXPLAINED — "it used to carry `?from=/clients/{id}`"
 * is a sentence worth keeping, and flagging it would train the next reader to delete the history
 * rather than the bug. The same false positive `switch-columns.test.ts` records hitting. Code in a
 * string is unaffected: only `//` and block comments go.
 */
export const withoutComments = (source: string): string =>
	source.replaceAll(/\/\*[\s\S]*?\*\//g, '').replaceAll(/(^|[^:"'`])\/\/.*$/gm, '$1');

/** Every record-route link in a snippet of source. */
export const linksInText = (source: string): string[] =>
	[...withoutComments(source).matchAll(LINK)].map(([, , expression]) => expression);

export const linksIn = (path: string): string[] => linksInText(readFileSync(path, 'utf8'));

const isApiContext = (
	template: string,
	before: string,
	firstSeg: string,
	fileIsApi: boolean
): boolean => {
	if (fileIsApi || template.includes('apiUrl()')) return true;
	if (/bookUrl\s*\(\s*$/.test(before)) return true;
	return API_ONLY_HEAD.has(firstSeg);
};

/** Interpolations sitting in uid-typed OpenAPI slots, from one template literal.
 *  Longer paths first so `/documents/requests/${x}/items/${y}` is not reported as `/documents/${x}`. */
const interpolationsInTemplate = (
	template: string,
	before: string,
	fileIsApi: boolean
): string[] => {
	const found: string[] = [];
	for (const { source, firstSeg } of uidSlotPatterns()) {
		if (!isApiContext(template, before, firstSeg, fileIsApi)) continue;
		const pat = new RegExp(source, 'g');
		for (const match of template.matchAll(pat)) {
			found.push(...match.slice(1).filter(Boolean));
		}
	}
	return found;
};

/**
 * `${base}/${id}` where `base` was assigned from an `apiUrl()` template that is a uid-typed
 * path with the last uid dropped. The catalogue form actions are this shape (models, providers
 * with a `{kind}` in between, commentary nested under a model); a contiguous
 * `/catalogue/models/${id}` never appears, which is why a path-only scan kept missing them.
 */
const splitInterpolations = (source: string): string[] => {
	const found: string[] = [];
	const roots = uidCollectionRoots().map((root) => new RegExp(`${root}$`));
	const assign = /(?:const|let)\s+(\w+)\s*=\s*`([^`]*)`/g;
	for (const match of source.matchAll(assign)) {
		const name = match[1];
		const template = match[2];
		if (!template.includes('apiUrl()')) continue;
		if (!roots.some((root) => root.test(template))) continue;
		const use = new RegExp(`\\$\\{${escapeRe(name)}\\}/(\\$\\{[^}]+\\})`, 'g');
		for (const used of source.matchAll(use)) found.push(used[1]);
	}
	return found;
};

/** Every uid-typed API record path in a snippet of source. */
export const apiUidPathsInText = (source: string): string[] => {
	const text = withoutComments(source);
	const fileIsApi = text.includes('apiUrl()') || /\bbookUrl\s*\(/.test(text);
	const found: string[] = [];
	const templates = /`([^`]*)`/g;
	for (const match of text.matchAll(templates)) {
		const template = match[1];
		const before = text.slice(Math.max(0, match.index! - 20), match.index);
		found.push(...interpolationsInTemplate(template, before, fileIsApi));
	}
	found.push(...splitInterpolations(text));
	return [...new Set(found)];
};

export const apiUidPathsIn = (path: string): string[] =>
	apiUidPathsInText(readFileSync(path, 'utf8'));

/**
 * Source files a link can be written in: routes and the shared components that build hrefs.
 * Tests are excluded — they assert on literal expected URLs, including the bad shapes they exist
 * to refuse.
 */
export const sources = (app: string): string[] =>
	[
		...globSync(`${portalRoot(app)}/routes/**/*.svelte`),
		...globSync(`${portalRoot(app)}/routes/**/*.ts`),
		...globSync(`${portalRoot(app)}/lib/**/*.svelte`),
		...globSync(`${portalRoot(app)}/lib/**/*.ts`)
	].filter((path) => !path.endsWith('.test.ts'));

/**
 * Every dynamic route segment in a portal, as `{route, name}`.
 *
 * A plain `**` walk filtered in JS rather than a `[*]` glob pattern: brackets are a glob character
 * class, so the literal directory name matches nothing — the same trap `book.test.ts` records, and
 * the reason the callers carry a guard-on-the-guard.
 */
export const dynamicSegments = (app: string): { route: string; name: string }[] =>
	globSync(`${portalRoot(app)}/routes/**`, { withFileTypes: true })
		.filter((e) => e.isDirectory() && e.name.startsWith('[') && e.name.endsWith(']'))
		.map((e) => {
			const dir = `${e.parentPath}/${e.name}`.replaceAll('\\', '/');
			return {
				route: `${app}:${dir.slice(dir.indexOf('/src/') + 1)}`,
				// `[slug=portalPage]` is a matcher; the param is the part before the `=`.
				name: e.name.slice(1, -1).split('=')[0]
			};
		});
