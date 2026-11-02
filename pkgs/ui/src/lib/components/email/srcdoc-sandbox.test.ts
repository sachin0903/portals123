/**
 * EVERY `srcdoc` IFRAME IN THE REPOSITORY IS SANDBOXED — repo-wide, because file-local was not enough.
 *
 * `email-preview-frame.svelte` owns `sandbox=""` and its header explains why at length. The pin on it
 * read only its OWN file, and its comment even anticipated "a second, laxer iframe" — but only one
 * added to that file. So when a version-history dialog grew a bare `<iframe srcdoc={html}>` in another
 * directory, nothing looked: 892 tests passed over it.
 *
 * That mattered because the body is author-written HTML the API deliberately does not sanitise (a mail
 * client runs no script, so the markup is policed at the canvas), and an un-sandboxed same-origin frame
 * runs an injected `<script>` or `on*` handler in the app's own origin under the reader's session — in
 * both directions, since a firm's stored version renders in a Graphene operator's admin origin and the
 * model tenant's schema is cloned verbatim into every new firm.
 *
 * The rule has two halves, and the second is the one a plausible "fix" gets wrong: a sandbox that
 * carries BOTH `allow-scripts` and `allow-same-origin` can reach out and remove its own sandbox
 * attribute, so the pair is equivalent to no sandbox at all.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles.
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const ROOTS = ['pkgs/ui/src', 'apps/platform/src', 'apps/admin/src', 'apps/client/src'];

function svelteFiles(dir: string, out: string[] = []): string[] {
	let entries: string[];
	try {
		entries = readdirSync(dir);
	} catch {
		return out; // an app that does not exist in this checkout
	}
	for (const entry of entries) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) svelteFiles(full, out);
		else if (entry.endsWith('.svelte')) out.push(full);
	}
	return out;
}

/** Each `<iframe …>` opening tag, comments stripped so the prose about sandboxes is not mistaken
 *  for one. */
function iframeTags(source: string): string[] {
	const code = source.replace(/<!--[\s\S]*?-->/g, '');
	return [...code.matchAll(/<iframe\b[\s\S]*?>/g)].map((m) => m[0]);
}

const ALL = ROOTS.flatMap((r) => svelteFiles(join(REPO_ROOT, r)));

/**
 * Every scanned file's iframe tags, extracted ONCE at collection time.
 *
 * Both cases below used to re-read all ~700 files and re-run the tag regex inside the test body.
 * Fast enough alone, and over vitest's 5s default the moment the machine is busy: the pre-push
 * hook runs the API's whole suite alongside this, and the first case took 18s and failed as a
 * TIMEOUT — which reads as "an unsandboxed frame shipped", not as "the disk was busy". The scan
 * belongs outside the timed body; the cases now filter a list in memory.
 */
const TAGGED: { file: string; tags: string[] }[] = ALL.map((file) => ({
	file: file.slice(REPO_ROOT.length),
	tags: iframeTags(readFileSync(file, 'utf8'))
})).filter((entry) => entry.tags.length > 0);

describe('every srcdoc iframe is sandboxed', () => {
	it('scans the real tree, so the assertions below cannot pass vacuously', () => {
		expect(ALL.length).toBeGreaterThan(200);
		expect(ALL.some((f) => f.endsWith('email-preview-frame.svelte'))).toBe(true);
	});

	it('carries a sandbox attribute wherever it renders srcdoc', () => {
		const unsandboxed: string[] = [];
		for (const { file, tags } of TAGGED) {
			for (const tag of tags) {
				if (!/\bsrcdoc\b/.test(tag)) continue;
				if (!/\bsandbox\b/.test(tag)) unsandboxed.push(file);
			}
		}
		expect(unsandboxed, 'these render author HTML in the app’s own origin').toEqual([]);
	});

	it('never pairs allow-scripts with allow-same-origin, which cancels the sandbox', () => {
		const escapes: string[] = [];
		for (const { file, tags } of TAGGED) {
			for (const tag of tags) {
				const sandbox = /\bsandbox=["']([^"']*)["']/.exec(tag);
				if (!sandbox) continue;
				const tokens = sandbox[1].split(/\s+/);
				if (tokens.includes('allow-scripts') && tokens.includes('allow-same-origin'))
					escapes.push(file);
			}
		}
		expect(escapes, 'a frame that can remove its own sandbox attribute').toEqual([]);
	});
});
