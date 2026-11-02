import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * Side drawers pin the header and footer and scroll only the body. `overflow-y-auto` on
 * `Sheet.Content` itself is the failure: the chrome scrolls away with the form.
 *
 * The primitive carries `overflow-hidden` on left/right. A caller that then sets `overflow-y-auto`
 * on Content wins (tailwind-merge, later class) and undoes it — so this scans every Svelte file
 * that opens a `Sheet.Content` tag.
 */
const here = fileURLToPath(new URL('.', import.meta.url));
const repo = join(here, '../../../../../../..');
const primitive = readFileSync(join(here, 'sheet-content.svelte'), 'utf8');

function svelteFiles(root: string): string[] {
	const out: string[] = [];
	// `.svelte-kit` is generated and holds `proxy+*` symlinks that dangle after a route
	// rename (screening → compliance) while `moon run :dev` is up — `statSync` then throws
	// ENOENT and this pin reads as a Sheet.Content regression. The other repo-wide source
	// walks (`srcdoc-sandbox`, `toaster-wiring`) already skip it.
	const skip = new Set(['node_modules', '.git', 'dist', 'build', '.svelte-kit']);
	const walk = (dir: string) => {
		for (const name of readdirSync(dir)) {
			if (skip.has(name)) continue;
			const path = join(dir, name);
			const st = statSync(path, { throwIfNoEntry: false });
			if (!st) continue;
			if (st.isDirectory()) walk(path);
			else if (name.endsWith('.svelte')) out.push(path);
		}
	};
	walk(root);
	return out;
}

function contentTags(source: string): string[] {
	const tags: string[] = [];
	const re = /<Sheet\.Content\b/g;
	for (let hit = re.exec(source); hit; hit = re.exec(source)) {
		let i = hit.index + hit[0].length;
		let depth = 0;
		for (; i < source.length; i++) {
			const ch = source[i];
			if (ch === '<') break;
			if (ch === '{') depth += 1;
			else if (ch === '}' && depth) depth -= 1;
			else if (ch === '>' && depth === 0) {
				tags.push(source.slice(hit.index, i + 1));
				break;
			}
		}
	}
	return tags;
}

describe('sheet footer', () => {
	it('lays action buttons in a row, not a stack', () => {
		const footer = readFileSync(join(here, 'sheet-footer.svelte'), 'utf8');
		expect(footer).toMatch(/flex-row/);
		expect(footer).toMatch(/items-center/);
		expect(footer).toMatch(/justify-end/);
		expect(footer).not.toMatch(/flex-col(?!-reverse)/);
	});
});

describe('sheet scroll', () => {
	it('clips left and right drawers so only an inner body can scroll', () => {
		expect(primitive).toMatch(/data-\[side=left\]:overflow-hidden/);
		expect(primitive).toMatch(/data-\[side=right\]:overflow-hidden/);
	});

	it('does not put overflow-y-auto on Sheet.Content', () => {
		const roots = ['apps', 'pkgs'].map((p) => join(repo, p));
		const offenders: string[] = [];
		for (const root of roots) {
			for (const file of svelteFiles(root)) {
				const tags = contentTags(readFileSync(file, 'utf8'));
				if (tags.some((t) => /\boverflow-y-auto\b/.test(t))) {
					offenders.push(relative(repo, file));
				}
			}
		}
		expect(offenders).toEqual([]);
	});
});

describe('sheet width', () => {
	const classLiteral = primitive.match(/'bg-popover[\s\S]*?'/)?.[0] ?? '';

	it('makes left/right drawers full width below sm, then the illustration width', () => {
		expect(classLiteral).toMatch(/data-\[side=left\]:w-full/);
		expect(classLiteral).toMatch(/data-\[side=right\]:w-full/);
		expect(classLiteral).not.toMatch(/data-\[side=left\]:w-3\/4/);
		expect(classLiteral).not.toMatch(/data-\[side=right\]:w-3\/4/);
		expect(classLiteral).toMatch(/data-\[side=left\]:sm:w-\[58vw\]/);
		expect(classLiteral).toMatch(/data-\[side=right\]:sm:w-\[58vw\]/);
		expect(classLiteral).not.toMatch(/\bsm:max-w-sm\b/);
	});

	it('does not pin a viewport fraction on a phone', () => {
		// `!w-[55vw]` with no `sm:` is 55% of a 375px screen. A console that needs the fraction
		// on desktop writes `sm:!w-[55vw]` so the primitive’s full-width still holds below sm.
		const roots = ['apps', 'pkgs'].map((p) => join(repo, p));
		const offenders: string[] = [];
		for (const root of roots) {
			for (const file of svelteFiles(root)) {
				const tags = contentTags(readFileSync(file, 'utf8'));
				if (tags.some((t) => /(?<!sm:)!w-\[\d+vw\]/.test(t))) {
					offenders.push(relative(repo, file));
				}
			}
		}
		expect(offenders).toEqual([]);
	});

	it('does not cap drawers narrower than the 58vw floor, except the form drawers', () => {
		// `sm:max-w-*` on a caller caps the primitive's `w-[58vw]`. The form drawers (payment /
		// withdraw / ad hoc fee / contribution / transfer / vulnerability / drawdown hand-off) are
		// the previous wide (`sm:max-w-xl`). A peek that is supposed to stay smaller (mobile nav, a
		// document rail) is named here.
		const formXl = new Set([
			'pkgs/ui/src/lib/components/book/accounts/instruction-sheet.svelte',
			'pkgs/ui/src/lib/components/book/accounts/model-sheet.svelte',
			'pkgs/ui/src/lib/components/book/contributions/contribution-form.svelte',
			'pkgs/ui/src/lib/components/book/transfers/transfer-form.svelte',
			'pkgs/ui/src/lib/components/book/clients/client-record.svelte',
			'pkgs/ui/src/lib/components/book/clients/clients-listing.svelte',
			'apps/platform/src/lib/components/accounts/account-record.svelte',
			'apps/admin/src/routes/integrations/IntegrationEditSheet.svelte'
		]);
		const peek = new Set([
			'pkgs/ui/src/lib/components/ui/sidebar/sidebar.svelte',
			'apps/client/src/routes/documents/[uid]/+page.svelte'
		]);
		const roots = ['apps', 'pkgs'].map((p) => join(repo, p));
		const offenders: string[] = [];
		for (const root of roots) {
			for (const file of svelteFiles(root)) {
				// `relative()` returns the PLATFORM separator, and both allowlists are written with
				// forward slashes — so on Windows every `has()` missed and all four `formXl` entries
				// were reported as offenders. The rule was never broken; the lookup was. (Found on a
				// rebase, where a red gate on two files the branch had never touched was the tell.)
				const rel = relative(repo, file).split(sep).join('/');
				if (peek.has(rel)) continue;
				const tags = contentTags(readFileSync(file, 'utf8'));
				const capped = tags.some((t) => /\bsm:max-w-(sm|md|lg|xl|2xl|3xl)\b/.test(t));
				if (!capped) continue;
				if (formXl.has(rel) && tags.every((t) => !/\bsm:max-w-(sm|md|lg|2xl|3xl)\b/.test(t)))
					continue;
				offenders.push(rel);
			}
		}
		expect(offenders).toEqual([]);
	});
});
