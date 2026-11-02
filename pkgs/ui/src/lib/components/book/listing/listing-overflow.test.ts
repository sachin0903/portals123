import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(join(import.meta.dirname, 'listing-overflow.svelte'), 'utf8');
const toolbar = readFileSync(join(import.meta.dirname, 'listing-toolbar.svelte'), 'utf8');
const index = readFileSync(join(import.meta.dirname, 'index.ts'), 'utf8');

describe('ListingOverflow', () => {
	it('is a kebab, not a second heading row of buttons', () => {
		expect(source).toContain('aria-label={label}');
		expect(source).toContain("label = 'Listing actions'");
		expect(source).toContain('ellipsis-vertical');
		expect(source).toContain('DropdownMenu.Content');
	});

	it('is exported from the listing barrel and slotted on the toolbar', () => {
		expect(index).toContain('ListingOverflow');
		// `ml-auto` is what parks it on the right of the search/filter row — the top-right of
		// the table, which is where a page action lives once the heading is gone.
		expect(toolbar).toContain('actions?: Snippet');
		expect(toolbar).toContain('ml-auto');
	});
});
