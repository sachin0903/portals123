import { describe, expect, it } from 'vitest';
import { isWebUrl } from './urls';

describe('isWebUrl', () => {
	it('loads and links only http(s) addresses', () => {
		expect(isWebUrl('https://www.bbc.co.uk/a.jpg')).toBe(true);
		expect(isWebUrl('http://example.com')).toBe(true);
		expect(isWebUrl('javascript:alert(1)')).toBe(false);
		expect(isWebUrl('data:image/png;base64,AAAA')).toBe(false);
		expect(isWebUrl('not a url')).toBe(false);
		expect(isWebUrl('')).toBe(false);
		expect(isWebUrl(null)).toBe(false);
	});
});
