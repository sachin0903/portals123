/**
 * Collapse `actions:read` and `actions:write` into one chip, `actions:read,write`.
 *
 * A grant with no verb (`system`) stays its own chip. Verbs stay in a fixed order so the chip
 * reads the same way wherever the slugs arrived sorted.
 */

const READ = 0;
const WRITE = 1;
const OTHER = 2;

function verbRank(verb: string): number {
	if (verb === 'read' || verb.startsWith('read_')) return READ;
	if (verb === 'write' || verb.startsWith('write_')) return WRITE;
	return OTHER;
}

export function groupPermissions(slugs: readonly string[]): string[] {
	const verbs = new Map<string, string[]>();
	const order: string[] = [];
	for (const slug of slugs) {
		const split = slug.indexOf(':');
		const resource = split > 0 ? slug.slice(0, split) : slug;
		const verb = split > 0 ? slug.slice(split + 1) : '';
		let list = verbs.get(resource);
		if (!list) {
			list = [];
			verbs.set(resource, list);
			order.push(resource);
		}
		if (verb && !list.includes(verb)) list.push(verb);
	}
	return order
		.sort((a, b) => a.localeCompare(b))
		.map((resource) => {
			const list = verbs.get(resource) ?? [];
			if (list.length === 0) return resource;
			list.sort((a, b) => verbRank(a) - verbRank(b) || a.localeCompare(b));
			return `${resource}:${list.join(',')}`;
		});
}
