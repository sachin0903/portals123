/** The shared editor loads its canvas assets by ABSOLUTE URL, so every app mounting `EmailEditor` must ship
 *  its own copy in `static/`: the components live in pkgs/ui and the assets cannot, and a missing file is a
 *  silent 404 that reads as a styling regression. Both halves — every consumer HAS them, and they are identical. */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { matchFields } from './merge-fields.js';

// fileURLToPath, not `.pathname`: on Windows the latter yields `/C:/…`, and every existsSync below reports false.
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));

const CONSUMERS = ['apps/platform', 'apps/admin'];

/** Loaded by absolute URL from `create.ts` — see the `<link>`, `canvas.styles` and `canvas.scripts`. */
const ASSETS = ['grapesjs.css', 'gjs-email-canvas.css', 'gjs-email-jinja-hl.js'];

describe('the shared email editor’s static canvas assets', () => {
	it('finds the repo root, so the assertions below cannot pass vacuously', () => {
		expect(existsSync(join(REPO_ROOT, 'pnpm-workspace.yaml'))).toBe(true);
	});

	it('exist in every app that mounts the editor', () => {
		const missing: string[] = [];
		for (const app of CONSUMERS)
			for (const asset of ASSETS)
				if (!existsSync(join(REPO_ROOT, app, 'static', asset)))
					missing.push(`${app}/static/${asset}`);
		expect(missing).toEqual([]);
	});

	it('are byte-identical across those apps, so a canvas fix cannot apply to only one', () => {
		for (const asset of ASSETS) {
			const [first, ...rest] = CONSUMERS.map((app) => ({
				app,
				text: readFileSync(join(REPO_ROOT, app, 'static', asset), 'utf8')
			}));
			for (const other of rest)
				expect(other.text, `${asset} differs between ${first.app} and ${other.app}`).toBe(
					first.text
				);
		}
	});
});

/** THE BUG THIS EXISTS FOR: both editors inject theme sheets guarded by an id and shared FIVE of them, drifted
 *  (94 lines against 188), so whichever mounts first in a session styles the other. Asserted as DISJOINTNESS. */
describe(
	'the two editors' + String.fromCharCode(8217) + ' injected stylesheets cannot collide',
	() => {
		const EDITORS = {
			email: 'pkgs/ui/src/lib/components/email-editor/create.ts',
			document: 'pkgs/ui/src/lib/components/template-authoring/editor/create.ts'
		} as const;

		function injectedIds(relPath: string): string[] {
			const source = readFileSync(join(REPO_ROOT, relPath), 'utf8');
			return [...source.matchAll(/\bs\.id = '([^']+)'/g)].map((m) => m[1] as string);
		}

		it('reads both editors, so the disjointness check cannot pass vacuously', () => {
			for (const [name, path] of Object.entries(EDITORS)) {
				expect(injectedIds(path).length, `${name} injects no stylesheet`).toBeGreaterThan(0);
			}
		});

		it('shares no stylesheet id between them', () => {
			const email = new Set(injectedIds(EDITORS.email));
			const shared = injectedIds(EDITORS.document).filter((id) => email.has(id));
			expect(shared, `shared stylesheet ids: ${shared.join(', ')}`).toEqual([]);
		});

		it('namespaces the email editor' + String.fromCharCode(8217) + 's own', () => {
			for (const id of injectedIds(EDITORS.email)) {
				expect(id, `${id} is not namespaced`).toMatch(/^gjs-(email|css)/);
			}
		});
	}
);

describe('the canvas assets are cache-busted while developing', () => {
	// `static/` assets fetched by the canvas IFRAME, outside Vite's module graph, so HMR cannot see them and edit
	// after edit reaches nobody. Dev only: `import.meta.env.DEV` is substituted, and a stamp would defeat caching.
	const create = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');

	it('stamps both assets, from one constant so they re-fetch together', () => {
		expect(create).toContain('canvasAsset(');
		expect(create).toMatch(/styles: \[canvasAsset\('\/gjs-email-canvas\.css'\)\]/);
		expect(create).toMatch(/scripts: \[canvasAsset\('\/gjs-email-jinja-hl\.js'\)\]/);
	});

	it('adds nothing in production', () => {
		const stamp = create.split('CANVAS_ASSET_STAMP =')[1]?.split(';')[0] ?? '';
		expect(stamp).toContain('DEV');
		expect(stamp).toContain(": ''");
	});
});

/** THE BEHAVIOURAL PIN for the one piece of LOGIC the canvas script duplicates.
 *
 *  `matching()` in `gjs-email-jinja-hl.js` is a hand-mirror of `merge-fields.ts::matchFields`,
 *  "mirrored because a classic script cannot import". The byte-identity check above holds the two
 *  APPS together and says nothing about whether either still agrees with the rail. They decide what
 *  an author sees — the canvas `{{` menu against the rail's Insert flyout — so a divergence shows the
 *  same query finding different fields in two places, with nothing failing.
 *
 *  Extracted and run rather than compared as text: the two are written in different styles (`var`
 *  loops against `filter`), so only behaviour can be compared. */
describe('the canvas script’s field matcher', () => {
	const script = readFileSync(
		join(REPO_ROOT, 'apps/platform/static/gjs-email-jinja-hl.js'),
		'utf8'
	);

	/** `function matching(query) { … }`, brace-counted so the body's own braces do not end it. */
	const matchingSource = (): string => {
		const start = script.indexOf('function matching(query)');
		expect(start, 'the canvas matcher is no longer named `matching`').toBeGreaterThan(-1);
		let depth = 0;
		for (let i = script.indexOf('{', start); i < script.length; i++) {
			if (script[i] === '{') depth++;
			else if (script[i] === '}' && --depth === 0) return script.slice(start, i + 1);
		}
		throw new Error('unbalanced braces in the canvas matcher');
	};

	type Matcher = (q: string) => { group: string; fields: { label: string; token: string }[] }[];
	const canvasMatcher = (): Matcher =>
		new Function('fieldGroups', `${matchingSource()}; return matching;`)(GROUPS) as Matcher;

	const GROUPS = [
		{
			group: 'General',
			fields: [
				{ label: 'First name', token: '{{ first_name }}' },
				{ label: 'Firm name', token: '{{ brand.name }}' }
			]
		},
		{ group: 'Document', fields: [{ label: 'Document name', token: '{{ document_name }}' }] }
	];

	it('extracts a runnable matcher, so the comparison below cannot pass vacuously', () => {
		expect(matchingSource()).toContain('toLowerCase');
		expect(canvasMatcher()('')).toHaveLength(2);
	});

	it('finds what the rail finds, for every query shape the rule covers', () => {
		const canvas = canvasMatcher();
		// Empty, a label substring, a token substring, a group name, mis-cased, and a miss — plus
		// `ocument`/`enera`, which match a GROUP name in the MIDDLE. Those two are the queries that
		// tell substring matching from prefix matching, and without them a matcher narrowed to
		// prefixes passes every other case: 'Document' is its own prefix and 'general' happens to
		// contain none of the shorter queries.
		for (const query of [
			'',
			'name',
			'first',
			'brand.',
			'Document',
			'DOCUMENT',
			'ocument',
			'enera',
			'nothing'
		]) {
			const mine = matchFields(GROUPS, query);
			const theirs = canvas(query);
			expect(
				theirs.map((g) => g.group),
				query
			).toEqual(mine.map((g) => g.group));
			expect(
				theirs.map((g) => g.fields.map((f) => f.token)),
				query
			).toEqual(mine.map((g) => g.fields.map((f) => f.token)));
		}
	});
});
