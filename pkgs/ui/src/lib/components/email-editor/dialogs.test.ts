import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Both dialogs were written inside the platform app and hoisted here so admin mounts the same ones rather than a copy; asserts nothing app-specific survived (`$app/*` import, hardcoded `/email-templates/…` URL, `?/update` name) against the real source, since svelte-check can't catch either. SSR markup isn't asserted — both are closed-by-default portalled `Dialog.Root`s. */
const HERE = import.meta.dirname;

/** Strips comments first — the `:` lookbehind spares `https://` — so the headers above, which name real routes as examples, can't trip their own match. */
const read = (name: string) =>
	readFileSync(join(HERE, name), 'utf8')
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');

const history = read('version-history-dialog.svelte');
const settings = read('template-settings-dialog.svelte');
const barrel = readFileSync(join(HERE, 'index.ts'), 'utf8');

describe('the email-template list dialogs are app-agnostic', () => {
	for (const [name, code] of [
		['version-history-dialog', history],
		['template-settings-dialog', settings]
	] as const) {
		it(`${name} imports no SvelteKit virtual, no package root, and no app route`, () => {
			// `$app/*` doesn't exist in this package — behaviour comes in as props — and `@graphene/ui` from inside pkgs/ui is a self-reference cycle; use `../ui/<part>/index.js`.
			expect(code).not.toMatch(/from '\$app\//);
			expect(code).not.toMatch(/from '\$lib\//);
			expect(code).not.toMatch(/from '@graphene\/ui/);
			expect(code).toMatch(/from '\.\.\/ui\//);
			expect(code).not.toMatch(/\/email-templates/);
		});
	}

	it('version history fetches, restores and hands navigation back through injected props', () => {
		// One injected endpoint serves both: GET lists, POST { vid } restores; nothing selected fetches nothing.
		expect(history).toMatch(/await fetch\(versionsUrl\)/);
		expect(history).toMatch(/await fetch\(versionsUrl,\s*\{\s*method: 'POST'/);
		expect(history).toMatch(/if \(!versionsUrl\) return;/);
		expect(history).toMatch(/open = false;\s*await onRestored\?\.\(\);/);
	});

	it('template settings posts to the host page action with the injected enhance', () => {
		// Success closes the dialog then lets the app re-run its load (`invalidateAll`).
		expect(settings).toMatch(/action=\{updateAction\}/);
		expect(settings).toMatch(/use:enhance=/);
		expect(settings).toMatch(/updateAction = '\?\/update'/); // the default the platform uses
		expect(settings).toMatch(/open = false;\s*await onSaved\?\.\(\);/);
	});

	it('asks for name, code and description — and never category again', () => {
		// No category control: the API update is `COALESCE(:cat, category)`, so posting any value here resets it on every edit. The `/category/i` check is deliberately broader than `name="category"` so a differently-shaped control still trips it.
		for (const field of ['name', 'code', 'description'])
			expect(settings).toMatch(new RegExp(`name="${field}"`));
		expect(settings).not.toMatch(/category/i);
	});

	it('exports both dialogs from the email-editor barrel', () => {
		expect(barrel).toMatch(
			/export \{ default as VersionHistoryDialog \} from '\.\/version-history-dialog\.svelte';/
		);
		expect(barrel).toMatch(
			/export \{ default as TemplateSettingsDialog \} from '\.\/template-settings-dialog\.svelte';/
		);
	});
});
