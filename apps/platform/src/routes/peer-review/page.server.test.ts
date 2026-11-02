/**
 * The old peer-review settings URL now sends the reader to Actions, which owns the switches.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

describe('the peer-review loader', () => {
	it('sends the old settings URL to Actions', () => {
		// `redirect` throws before the loader returns a promise.
		expect(() => load({} as never)).toThrowError(
			expect.objectContaining({ status: 308, location: '/actions' })
		);
	});
});
