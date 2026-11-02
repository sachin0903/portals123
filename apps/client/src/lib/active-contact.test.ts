/**
 * `contact_id` is forwarded to the API by every proxy on this portal, from four different carriers
 * (query string, JSON body, multipart field, cookie). It used to be spliced into the API URL raw,
 * so a body value like `"1&scope=all"` rewrote the query the API received. `parseContactId` is the
 * one place a raw value becomes a number, and these pin what it lets through.
 */
import { describe, expect, it } from 'vitest';
import { activeContactId, contactIdQuery, parseContactId } from './active-contact';

describe('parseContactId', () => {
	it('accepts a positive integer, as a number or as digits', () => {
		expect(parseContactId(7)).toBe(7);
		expect(parseContactId('7')).toBe(7);
		expect(parseContactId(' 42 ')).toBe(42);
	});

	it('rejects anything that is not a bare positive integer', () => {
		for (const raw of [
			'1&scope=all',
			'1#',
			'1/2',
			'-1',
			'0',
			'1.5',
			'1e3',
			'abc',
			'',
			'  ',
			null,
			undefined,
			true,
			{ id: 1 },
			1.5,
			0,
			-3,
			Number.NaN,
			'9'.repeat(19)
		]) {
			expect(parseContactId(raw), String(raw)).toBeNull();
		}
	});
});

describe('contactIdQuery', () => {
	it('is a leading query string, or nothing', () => {
		expect(contactIdQuery('12')).toBe('?contact_id=12');
		expect(contactIdQuery(12)).toBe('?contact_id=12');
		expect(contactIdQuery('12&x=1')).toBe('');
		expect(contactIdQuery(undefined)).toBe('');
	});
});

describe('activeContactId', () => {
	it('reads the same rule off a URL', () => {
		expect(activeContactId(new URL('http://p.test/x?contact_id=3'))).toBe(3);
		expect(activeContactId(new URL('http://p.test/x?contact_id=three'))).toBeNull();
		expect(activeContactId(new URL('http://p.test/x'))).toBeNull();
	});
});
