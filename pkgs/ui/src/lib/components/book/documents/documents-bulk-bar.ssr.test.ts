import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import DocumentsBulkBar from './documents-bulk-bar.svelte';

const bar = (props: Partial<Parameters<typeof render<typeof DocumentsBulkBar>>[1]['props']> = {}) =>
	render(DocumentsBulkBar, {
		props: { count: 0, documents: 0, files: 0, onclear: () => {}, ...props }
	}).body.replace(/<!--.*?-->/g, '');

describe('DocumentsBulkBar', () => {
	it('is absent with nothing selected', () => {
		expect(bar()).not.toContain('selected');
	});

	it('counts the selection and offers each Delete it has a callback and targets for', () => {
		const out = bar({
			count: 4,
			documents: 2,
			files: 1,
			ondeletedocuments: () => {},
			ondeletefiles: () => {}
		});
		expect(out).toContain('4 selected');
		expect(out).toMatch(/Delete 2\s*</);
		expect(out).toMatch(/Delete 1\s*file/);
		expect(out).toContain('Clear');
		// Withheld with no callback or nothing to act on.
		expect(bar({ count: 1, documents: 0, files: 1 })).not.toContain('Delete');
	});
});
