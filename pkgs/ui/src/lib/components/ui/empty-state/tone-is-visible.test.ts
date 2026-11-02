/**
 * A WARNING TONE MUST BE VISIBLE — a static scan across all three portals and this package.
 *
 * `empty-state.svelte` paints the warning on its CONTAINER (`!chromeless && (tone === 'warning'
 * ? 'border-warning-line bg-warning-subtle' : 'border-dashed')`). So `chromeless` and
 * `tone="warning"` together produce a failure that renders exactly like an absence — the one
 * distinction the tone exists to draw, silently discarded by the prop meant to tidy the frame.
 *
 * It is not a hypothetical: three states shipped that way in the same sweep that introduced the
 * tone, and none of them could be seen to be wrong — the prop was accepted, the markup rendered,
 * the gates were green, and the box still said "nothing here" in dashed grey over a failed read.
 * A rendering test on one component cannot catch the next one; a scan can.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
// HERE is pkgs/ui/src/lib/components/ui/empty-state/ — seven levels to the workspace root.
const REPO = join(HERE, '..', '..', '..', '..', '..', '..', '..');
const ROOTS = [
	join(REPO, 'pkgs', 'ui', 'src'),
	join(REPO, 'apps', 'platform', 'src'),
	join(REPO, 'apps', 'admin', 'src'),
	join(REPO, 'apps', 'client', 'src')
];

function svelteFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) svelteFiles(full, out);
		else if (entry.endsWith('.svelte')) out.push(full);
	}
	return out;
}

/** Each `<EmptyState …>` invocation, up to its own close. */
function invocations(source: string): { text: string; line: number }[] {
	const found: { text: string; line: number }[] = [];
	for (const match of source.matchAll(/<EmptyState\b/g)) {
		const from = match.index ?? 0;
		const tail = source.slice(from);
		const close = tail.indexOf('</EmptyState>');
		const selfClose = tail.indexOf('/>');
		const end =
			close !== -1 && (selfClose === -1 || close < selfClose)
				? close + '</EmptyState>'.length
				: selfClose !== -1
					? selfClose + 2
					: 800;
		found.push({ text: tail.slice(0, end), line: source.slice(0, from).split('\n').length });
	}
	return found;
}

describe('an EmptyState that says it is a warning must look like one', () => {
	it('no invocation asks for `chromeless` and a warning tone at once', () => {
		const offenders: string[] = [];
		for (const root of ROOTS) {
			for (const file of svelteFiles(root)) {
				const source = readFileSync(file, 'utf8');
				if (!source.includes('<EmptyState')) continue;
				for (const { text, line } of invocations(source)) {
					if (!text.includes('chromeless')) continue;
					// `tone="warning"` outright, or any ternary that can resolve to it.
					if (/tone=\{?[^}]*['"]warning['"]/.test(text)) {
						offenders.push(
							`${file
								.slice(REPO.length + 1)
								.split(sep)
								.join('/')}:${line}`
						);
					}
				}
			}
		}
		expect(offenders, 'the warning is painted on the container `chromeless` removes').toEqual([]);
	});
});
