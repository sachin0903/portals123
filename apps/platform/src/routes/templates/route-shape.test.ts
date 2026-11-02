/**
 * The shape of the templates surface.
 *
 * ONE page serves both kinds, fetching only the kind in `?kind=`. `/templates/emails/` therefore
 * holds NO listing of its own; what lives under it is the email EDITOR and its token-safe proxies,
 * which are real routes because opening an editor genuinely is a page load.
 *
 * WHY THIS IS TESTED ON DISK. Two things could break it quietly, and neither is visible to the
 * type-checker:
 *
 *  1. `/templates/emails/[uid]/…` sits beside `/templates/[uid]/…`, so the editor is reachable only
 *     because SvelteKit matches a static segment ahead of a dynamic one. Rename or move that
 *     directory and every link falls through to `/templates/[uid]`, which renders "template not
 *     found" rather than 404ing honestly.
 *  2. A template whose uid IS the reserved segment would be unreachable. `app/ids.py::new_id` mints
 *     a bare 12-character nanoid, so a 6-character uid cannot exist — this asserts the reserved
 *     segments stay shorter than a uid, which is what keeps that impossible rather than unlikely.
 */

import { existsSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const HERE = new URL('.', import.meta.url).pathname.replace(/^\/(\w:)/, '$1');

/** The public id length `app/ids.py::new_id` mints (docs: CLAUDE.md → Ids). */
const UID_LENGTH = 12;

describe('the templates surface', () => {
	it('serves both kinds from the one page', () => {
		expect(existsSync(join(HERE, '+page.svelte')), '/templates page').toBe(true);
		expect(existsSync(join(HERE, '+page.server.ts')), '/templates loader').toBe(true);
	});

	it('keeps no second listing under emails — the switch is a view, not a route', () => {
		// A `+page` here would be a second door to the same listing, reachable by URL, loading its own
		// copy of the data the parent already holds.
		const emails = join(HERE, 'emails');
		expect(existsSync(join(emails, '+page.svelte')), 'emails/+page.svelte').toBe(false);
		expect(existsSync(join(emails, '+page.server.ts')), 'emails/+page.server.ts').toBe(false);
	});

	it('holds the email editor and its proxies as static siblings, not dynamic ids', () => {
		// A listing whose links point at a path that no longer exists is the failure this file is
		// about — these are what `emailLinks` in the page addresses.
		const emails = join(HERE, 'emails', '[uid]');
		for (const child of ['edit', 'images', 'preview', 'send-test', 'versions']) {
			expect(existsSync(join(emails, child)), `emails/[uid]/${child}`).toBe(true);
		}
	});

	it('keeps every static child too short to be a template uid', () => {
		// A static sibling of `[uid]` shadows the uid that spells it. Nothing can spell one today
		// because a uid is exactly 12 characters, and this is what stops a future 12-character
		// segment being added without anyone noticing it takes a template's address away.
		const statics = readdirSync(HERE, { withFileTypes: true })
			.filter((e) => e.isDirectory() && !e.name.startsWith('['))
			.map((e) => e.name);
		expect(statics).toContain('emails');
		for (const name of statics) {
			expect(name.length, `static route segment "${name}"`).toBeLessThan(UID_LENGTH);
		}
	});

	it('is a directory of routes, not a file — so the checks above mean something', () => {
		// Guards the rest: if this path stopped being the routes directory they would pass by reading
		// nothing.
		expect(statSync(HERE).isDirectory()).toBe(true);
	});
});
