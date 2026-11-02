import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import PaginationStrip from './pagination-strip.svelte';

describe('PaginationStrip', () => {
	it('keeps the range left and the shared pager on the right', () => {
		const { body } = render(PaginationStrip, {
			props: { label: '26–50 of 83', page: 2, pageCount: 4 }
		});

		expect(body).toContain('data-slot="listing-pagination"');
		expect(body).toContain('26–50 of 83');
		expect(body).toContain('ml-auto min-w-0');
		expect(body.indexOf('26–50 of 83')).toBeLessThan(body.indexOf('ml-auto min-w-0'));
	});
});
