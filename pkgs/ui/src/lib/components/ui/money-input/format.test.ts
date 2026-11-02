import { describe, expect, it } from 'vitest';
import { caretFor, groupDigits, liveFormat, parseAmount, settled, significant } from './format';

/**
 * The money field groups thousands AS YOU TYPE, which is only safe if the caret is re-anchored
 * afterwards. These pin both halves: what the box shows mid-entry, and where the caret ends up.
 */

describe('liveFormat', () => {
	const gbp = { precision: 2 };

	it('groups the integer part while it is being typed', () => {
		// The reason the feature exists: an unseparated 1234500 is misread by a factor of ten, and
		// it is misread while it is being written, not after.
		expect(liveFormat('1234500', gbp)).toBe('1,234,500');
		expect(liveFormat('1234', gbp)).toBe('1,234');
		expect(liveFormat('999', gbp)).toBe('999');
	});

	it('re-groups text that already carries separators', () => {
		// Every keystroke re-formats the whole box, so its own previous output is the usual input.
		expect(liveFormat('1,234', gbp)).toBe('1,234');
		expect(liveFormat('1,2345', gbp)).toBe('12,345');
	});

	it('keeps a trailing decimal point', () => {
		// "1." is a state you must pass through to reach "1.5". Dropping it makes the point
		// impossible to type: it would vanish on the keystroke that entered it.
		expect(liveFormat('1.', gbp)).toBe('1.');
		expect(liveFormat('1234.', gbp)).toBe('1,234.');
	});

	it('neither pads nor rounds the pence', () => {
		// "1.50" has to be reachable, so the 0 typed after "1.5" must not be normalised away.
		expect(liveFormat('1.5', gbp)).toBe('1.5');
		expect(liveFormat('1.50', gbp)).toBe('1.50');
	});

	it('refuses a digit past the precision rather than rounding', () => {
		// Rounding a figure someone is halfway through writing changes the amount under them.
		expect(liveFormat('1.567', gbp)).toBe('1.56');
		expect(liveFormat('1.999', gbp)).toBe('1.99');
	});

	it('drops a second decimal point', () => {
		expect(liveFormat('1.2.3', gbp)).toBe('1.23');
	});

	it('accepts a pasted formatted amount', () => {
		expect(liveFormat('£1,234.50', gbp)).toBe('1,234.50');
	});

	it('has no decimal point at all for a zero-decimal currency', () => {
		// JPY has no pence. A field that let one be typed would show an amount the currency
		// cannot express.
		expect(liveFormat('1200.5', { precision: 0 })).toBe('1,200');
		expect(liveFormat('1200.', { precision: 0 })).toBe('1,200');
	});

	it('keeps a minus only where a negative is representable', () => {
		expect(liveFormat('-500', { precision: 2, signed: true })).toBe('-500');
		// The default: a field with min 0 (a contribution, a transfer value) must not even show one.
		expect(liveFormat('-500', gbp)).toBe('500');
	});

	it('is empty for an empty box', () => {
		expect(liveFormat('', gbp)).toBe('');
		expect(liveFormat('abc', gbp)).toBe('');
	});
});

describe('caret', () => {
	it('counts digits and the point, not separators', () => {
		expect(significant('1,234')).toBe(4);
		expect(significant('1,234.5')).toBe(6);
		expect(significant('£')).toBe(0);
	});

	it('keeps the caret behind the same digits when a separator appears', () => {
		// Typing the 4 in "123|" → "1,234": four significant characters precede the caret, and in
		// the regrouped string that is position 5, past the comma. Anchoring on the raw index would
		// have left it at 4 — between the 3 and the 4.
		expect(caretFor('1,234', significant('1234'))).toBe(5);
	});

	it('keeps the caret mid-number when a digit is inserted in the middle', () => {
		// "1,234,500" with the caret after the 2, typing 9: raw becomes "1,29|34,500", four
		// significant characters deep, which in "1,293,4500"… is not the point — the point is that
		// the caret stays behind those four, wherever the separators land.
		const regrouped = liveFormat('1,2934,500', { precision: 2 });
		expect(regrouped).toBe('12,934,500');
		expect(caretFor(regrouped, significant('1,29'))).toBe(4);
		expect(regrouped.slice(0, 4)).toBe('12,9');
	});

	it('sits at the start for an empty anchor, but after a minus sign', () => {
		expect(caretFor('1,234', 0)).toBe(0);
		expect(caretFor('-500', 0)).toBe(1);
	});

	it('lands at the end when the anchor is past every digit', () => {
		// What happens when a keystroke is refused (a third decimal place): the caret must stay
		// where it was rather than jumping to the front.
		expect(caretFor('1.56', significant('1.567'))).toBe(4);
	});
});

describe('settled', () => {
	it('pads to the currency precision and groups', () => {
		expect(settled(1234500, 2)).toBe('1,234,500.00');
		expect(settled(1.5, 2)).toBe('1.50');
		expect(settled(1200, 0)).toBe('1,200');
	});

	it('is empty for no amount — not zero', () => {
		// A blank fee and a fee of zero are different facts.
		expect(settled(null, 2)).toBe('');
		expect(settled(undefined, 2)).toBe('');
		expect(settled(NaN, 2)).toBe('');
	});
});

describe('parseAmount', () => {
	it('reads the number behind the display string', () => {
		expect(parseAmount('1,234.50')).toBe(1234.5);
		expect(parseAmount('£1,234.50')).toBe(1234.5);
	});

	it('is null for the states that are not yet an amount', () => {
		expect(parseAmount('')).toBeNull();
		expect(parseAmount('.')).toBeNull();
		expect(parseAmount('-')).toBeNull();
	});

	it('reads a trailing point as the number typed so far', () => {
		expect(parseAmount('1.')).toBe(1);
	});

	it('clamps to the declared bounds', () => {
		expect(parseAmount('-5', { min: 0 })).toBe(0);
		expect(parseAmount('500', { max: 100 })).toBe(100);
	});

	it('keeps zero as zero', () => {
		// It must not fall through a falsy check into null: zero is an amount.
		expect(parseAmount('0')).toBe(0);
	});
});

describe('groupDigits', () => {
	it('separates from the right', () => {
		expect(groupDigits('1')).toBe('1');
		expect(groupDigits('1000')).toBe('1,000');
		expect(groupDigits('1234567')).toBe('1,234,567');
		expect(groupDigits('')).toBe('');
	});
});
