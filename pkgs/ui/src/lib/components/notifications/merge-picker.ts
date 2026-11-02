/**
 * Pure logic for the notification-rule merge-field picker (the "Available variables" panel).
 *
 * The panel groups a trigger's params by namespace, lets the author search, ticks the ones already
 * used in the copy, and warns (client-side only) about tokens the copy uses that the trigger does
 * not provide. All of it is pure so it can be unit-tested away from the Svelte rendering.
 *
 * The unknown-token warning is DELIBERATELY advisory, never a block: the server `_validate` must not
 * refuse unknown tokens, because a stored rule may legitimately carry a token a later catalogue
 * dropped — refusing it would make an existing rule un-saveable. This mirrors `render.py`, which
 * simply drops an unresolved token.
 */
import type { CatalogueParam } from './rule-types.js';

// A merge token: `{` + a dotted lowercase path + `}` — the same shape app/notifications/render.py
// recognises. Case-insensitive so `{Client.Name}` is caught as a (mis-cased) use, not missed.
const TOKEN = /\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}/gi;

/** The lowercased tokens (`client.name`, `event.when`) actually present in the authored copy. */
export function tokensIn(...copies: (string | null | undefined)[]): Set<string> {
	const out = new Set<string>();
	for (const copy of copies) {
		if (!copy) continue;
		for (const match of copy.matchAll(TOKEN)) {
			const key = match[1];
			if (key) out.add(key.toLowerCase());
		}
	}
	return out;
}

export interface ParamGroup {
	/** The key namespace — the part before the first dot (`client`, `event`, `firm`). */
	namespace: string;
	label: string;
	params: CatalogueParam[];
}

// Human labels + a stable display order for the namespaces the catalogue produces. An unknown
// namespace (a future param family) trails alphabetically with a capitalised fallback label.
const NAMESPACE_LABELS: Record<string, string> = {
	client: 'Client',
	adviser: 'Adviser',
	account: 'Account',
	task: 'Task',
	document: 'Document',
	actor: 'Who did it',
	event: 'Event',
	firm: 'Firm'
};
const NAMESPACE_ORDER = [
	'client',
	'adviser',
	'account',
	'task',
	'document',
	'actor',
	'event',
	'firm'
];

function capitalise(value: string): string {
	return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Group params by namespace (the part before the first dot), in a stable, human order; params
 *  within a group keep their catalogue order. Empty groups never appear. */
export function groupParams(params: CatalogueParam[]): ParamGroup[] {
	const byNamespace = new Map<string, CatalogueParam[]>();
	for (const param of params) {
		const namespace = param.key.split('.')[0] || param.key;
		const existing = byNamespace.get(namespace);
		if (existing) existing.push(param);
		else byNamespace.set(namespace, [param]);
	}
	const rank = (namespace: string) => {
		const index = NAMESPACE_ORDER.indexOf(namespace);
		return index === -1 ? NAMESPACE_ORDER.length : index;
	};
	return [...byNamespace.entries()]
		.sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
		.map(([namespace, groupParams]) => ({
			namespace,
			label: NAMESPACE_LABELS[namespace] ?? capitalise(namespace),
			params: groupParams
		}));
}

/** Filter params by a search query (matches the human label OR the dotted key), case-insensitive.
 *  A blank query returns the list unchanged. */
export function filterParams(params: CatalogueParam[], query: string): CatalogueParam[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return params;
	return params.filter(
		(param) =>
			param.label.toLowerCase().includes(needle) || param.key.toLowerCase().includes(needle)
	);
}

/** Tokens the copy USES that the trigger does NOT provide — the advisory (never blocking) warning.
 *  Sorted for a stable render. */
export function unknownTokens(used: Set<string>, params: CatalogueParam[]): string[] {
	const known = new Set(params.map((param) => param.key.toLowerCase()));
	return [...used].filter((token) => !known.has(token)).sort();
}
