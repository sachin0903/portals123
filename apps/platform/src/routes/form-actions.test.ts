/**
 * Every `?/action` a page posts to must be an action that page declares.
 *
 * THE DEFECT THIS EXISTS FOR. `/documents/requests` posted to `?/createForClient` whenever the
 * builder arrived with a client already chosen — an action no `+page.server.ts` declares, which
 * SvelteKit answers 404. That is the *only* path a "Raise a request" from a document record takes,
 * so the feature was broken on its whole reason for existing, and **every test passed**: the action
 * tests call `actions.create(...)` as a function and never go through the attribute the form
 * actually submits. Nothing else could have caught it short of clicking the button.
 *
 * It is a static scan because that is what the gap is: the two halves of a form post (the `action=`
 * string in the markup and the exported `actions` object) are matched by SvelteKit at RUNTIME on a
 * name, with no type relating them. A renamed or invented action typechecks perfectly.
 *
 * Only LITERAL action strings are checked. A component that takes its action as a PROP (the shared
 * `RequestDialog`, mounted by three routes) is deliberately out of scope here — the literal lives at
 * the mount site, which is a page, which is what this scans.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROUTES = join(import.meta.dirname);

/** Every `+page.svelte` under `src/routes`, with its sibling `+page.server.ts` if there is one. */
function pages(dir: string, found: { page: string; server: string | null }[] = []) {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory()) pages(full, found);
		else if (entry.name === '+page.svelte') {
			const server = join(dir, '+page.server.ts');
			let hasServer = true;
			try {
				readFileSync(server);
			} catch {
				hasServer = false;
			}
			found.push({ page: full, server: hasServer ? server : null });
		}
	}
	return found;
}

/** `'?/create'` / `"?/create"` — the literal form-action references in a page's markup. */
function referenced(source: string): string[] {
	const hits = [...source.matchAll(/['"]\?\/([a-zA-Z_][\w]*)['"]/g)].map((m) => m[1]);
	return [...new Set(hits)];
}

/** `\tname: async ({ … }) => {` — the actions a `+page.server.ts` exports. Indent-anchored, which is
 *  what every action in this app looks like inside `export const actions = { … }`. */
function declared(source: string): string[] {
	return [...source.matchAll(/^\t([a-zA-Z_][\w]*)\s*:\s*async/gm)].map((m) => m[1]);
}

describe('form actions: every ?/action posted to is declared', () => {
	it('has no page pointing at an action that does not exist', () => {
		const broken: string[] = [];
		for (const { page, server } of pages(ROUTES)) {
			const refs = referenced(readFileSync(page, 'utf8'));
			if (refs.length === 0) continue;
			const relative = page.slice(ROUTES.length + 1).replace(/\\/g, '/');
			if (!server) {
				broken.push(`${relative} posts to ${refs.join(', ')} but has no +page.server.ts`);
				continue;
			}
			const has = declared(readFileSync(server, 'utf8'));
			const missing = refs.filter((r) => !has.includes(r));
			if (missing.length > 0) {
				broken.push(
					`${relative} posts to ?/${missing.join(', ?/')} — declared: ${has.join(', ') || '(none)'}`
				);
			}
		}
		expect(broken, `these form posts would 404:\n  ${broken.join('\n  ')}`).toEqual([]);
	});

	it('actually reads the routes (so an empty pass cannot be a broken scan)', () => {
		// A scan that silently finds nothing passes for ever. This pins that the walk reaches real
		// pages and that the two matchers both fire on the known-good case.
		const all = pages(ROUTES);
		expect(all.length).toBeGreaterThan(20);
		const withRefs = all.filter(({ page }) => referenced(readFileSync(page, 'utf8')).length > 0);
		expect(withRefs.length).toBeGreaterThan(3);
		const requests = all.find(({ page }) => page.includes(join('documents', 'requests')));
		expect(requests?.server).toBeTruthy();
		expect(referenced(readFileSync(requests!.page, 'utf8'))).toContain('create');
		expect(declared(readFileSync(requests!.server!, 'utf8'))).toContain('create');
	});
});
