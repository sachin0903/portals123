/**
 * A PAGE TITLE ON ITS OWN IS HALF A PAGE.
 *
 * The same rule `empty-state.svelte` states for an empty box: a heading names the screen, the
 * sentence under it says what is ON the screen. Book *listings* no longer use PageHeader — the
 * topbar crumb is the title. This pin is for the screens that still have one (records, settings,
 * wizards): a title without a description is half a page.
 *
 * A scan rather than a render, because the defect is an OMISSION: rendering a page proves what it
 * says, and no amount of rendering proves that the next page added will say anything.
 *
 * `EXEMPT` is where a genuine exception is recorded with its reason, so that a page skipping the
 * description is a decision somebody wrote down rather than one nobody noticed.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
// HERE is pkgs/ui/src/lib/components/shell/ — six levels to the workspace root.
const REPO = join(HERE, '..', '..', '..', '..', '..', '..');
const ROOTS = [
	join(REPO, 'pkgs', 'ui', 'src'),
	join(REPO, 'apps', 'platform', 'src'),
	join(REPO, 'apps', 'admin', 'src'),
	join(REPO, 'apps', 'client', 'src')
];

/**
 * Pages whose heading IS the content, so a standing description would be filler:
 * a RECORD page titled by the thing it shows (a task's own title, a person's name) has nothing
 * general to say about itself, and the record below is the description.
 */
const EXEMPT = new Set([
	// The heading is the task's own title.
	'pkgs/ui/src/lib/components/book/tasks/task-detail.svelte'
]);

function svelteFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) svelteFiles(full, out);
		else if (entry.endsWith('.svelte')) out.push(full);
	}
	return out;
}

/** Each `<PageHeader …>` opening tag, with the line it starts on. */
function headers(source: string): { tag: string; line: number }[] {
	const found: { tag: string; line: number }[] = [];
	for (const match of source.matchAll(/<PageHeader\b/g)) {
		const from = match.index ?? 0;
		const close = source.indexOf('>', from);
		found.push({
			tag: source.slice(from, close === -1 ? from + 400 : close + 1),
			line: source.slice(0, from).split('\n').length
		});
	}
	return found;
}

/** A title written as a `{…}` expression is a RECORD page naming its own subject. */
const isRecordTitle = (tag: string) => /title=\{/.test(tag);

describe('every page header says what the page holds', () => {
	it('no PageHeader carries a title without a description', () => {
		const offenders: string[] = [];
		for (const root of ROOTS) {
			for (const file of svelteFiles(root)) {
				const source = readFileSync(file, 'utf8');
				if (!source.includes('<PageHeader')) continue;
				const rel = file
					.slice(REPO.length + 1)
					.split(sep)
					.join('/');
				if (EXEMPT.has(rel)) continue;
				for (const { tag, line } of headers(source)) {
					if (!/title=/.test(tag)) continue;
					if (isRecordTitle(tag)) continue;
					if (/description=/.test(tag)) continue;
					offenders.push(`${rel}:${line}`);
				}
			}
		}
		expect(
			offenders,
			'a heading names the screen; the sentence under it says what is on the screen'
		).toEqual([]);
	});
});
