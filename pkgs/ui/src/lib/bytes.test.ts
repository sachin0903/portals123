import { expect, it } from 'vitest';
import { formatBytes } from './bytes';

const MB = 1024 * 1024;

it.each([
	[0, '0 B'],
	[1023, '1023 B'],
	[1024, '1 KB'],
	[412 * 1024, '412 KB'],
	[Math.round(24.6 * MB), '24.6 MB'],
	[2 * 1024 * MB, '2.0 GB'],
	[null, '—'],
	[Number.NaN, '—'],
	[-1, '—']
])('formatBytes(%s) is %s', (bytes, out) => {
	expect(formatBytes(bytes)).toBe(out);
});

it('reads an absent size as `empty`', () => {
	expect(formatBytes(undefined, '')).toBe('');
});
