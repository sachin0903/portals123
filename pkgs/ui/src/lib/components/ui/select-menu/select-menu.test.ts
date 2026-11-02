import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { render } from 'svelte/server';
import SelectMenu from './select-menu.svelte';

describe('SelectMenu trigger (SSR)', () => {
	it('never re-gains line-clamp on the value slot', () => {
		// `line-clamp` forces `display: -webkit-box`, which fights the `display: flex` the
		// truncation in select-menu.svelte depends on (see its own comment) — together they
		// stopped a long option label from ellipsizing and let it wrap onto a second line
		// instead, growing every dialog that hosts one. Regression case, not a style nit.
		const body = render(SelectMenu, {
			props: {
				items: [{ value: 'a', label: 'A'.repeat(80) }],
				value: 'a'
			}
		}).body;
		expect(body).not.toContain('line-clamp');
	});
});

describe('SelectMenu portal', () => {
	it('can render its panel inline so a Dialog focus trap still receives clicks', () => {
		const source = readFileSync(
			fileURLToPath(new URL('./select-menu.svelte', import.meta.url)),
			'utf8'
		);
		expect(source).toContain('portal = true');
		expect(source).toContain('portalProps={portal ? undefined : { disabled: true }}');
	});
});
