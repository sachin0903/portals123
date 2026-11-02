/** `email_defaults` (Python) writes the attributes the shipped defaults carry and this side reads them
 *  back; a mismatch is silent. The literals below are verbatim from `tests/test_email/defaults.py`. */
import { describe, expect, it } from 'vitest';
import { decodeRepeat, decodeVisibility, encodeRepeat, encodeVisibility } from './attrs';
import { ruleExpression } from './model';

/** Verbatim. Do not regenerate — re-encoding with our own helper would pass whatever it did. */
const REQUEST_REPEAT = '%7B%22field%22%3A%22item_titles%22%2C%22alias%22%3A%22item%22%7D';

const NOTE_GUARD =
	'%7B%22action%22%3A%22show%22%2C%22match%22%3A%22all%22%2C%22rules%22%3A%5B%7B%22field%22%3A' +
	'%22message%22%2C%22op%22%3A%22set%22%2C%22filter%22%3A%22paragraphs%22%7D%5D%7D';

describe('the shipped request list', () => {
	it('decodes to the repeat the panel edits', () => {
		expect(decodeRepeat(REQUEST_REPEAT)).toEqual({ field: 'item_titles', alias: 'item' });
	});

	it('is byte-identical to what this side would write', () => {
		expect(encodeRepeat({ field: 'item_titles', alias: 'item' })).toBe(REQUEST_REPEAT);
	});
});

describe('the shipped note guard', () => {
	it('decodes with its filter intact', () => {
		const vis = decodeVisibility(NOTE_GUARD);
		expect(vis?.rules).toEqual([{ field: 'message', op: 'set', filter: 'paragraphs' }]);
	});

	it('is byte-identical to what this side would write', () => {
		expect(
			encodeVisibility({
				action: 'show',
				match: 'all',
				rules: [{ field: 'message', op: 'set', filter: 'paragraphs' }]
			})
		).toBe(NOTE_GUARD);
	});

	it('compiles back to the Jinja the send shape spells out', () => {
		const vis = decodeVisibility(NOTE_GUARD);
		expect(ruleExpression(vis!.rules[0])).toBe('message | paragraphs');
	});
});
