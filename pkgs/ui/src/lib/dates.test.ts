// Holds in every host timezone: date-only cases read in UTC, the timestamp in the host's own zone.
import { expect, it } from 'vitest';
import { formatDate, formatDateTime, formatDayMonth } from './dates';

const nearMidnight = '2026-08-05T23:30:00Z';
const localDay = new Date(nearMidnight).getDate();

it('formatDate reads a date-only value in UTC and a timestamp in the reader’s zone', () => {
	expect(formatDate('2026-08-05')).toBe('5 Aug 2026');
	expect(formatDate(nearMidnight)).toBe(`${localDay} Aug 2026`);
	expect(formatDate('2026-09-13T09:00:00Z')).toBe('13 Sept 2026');
	expect(formatDayMonth('2026-12-31')).toBe('31 Dec');
});

it('reads an absent or unparseable value as `empty`, defaulting to an em dash', () => {
	expect([null, '', 'not a date'].map((v) => formatDate(v))).toEqual(['—', '—', '—']);
	expect(formatDate('junk', '')).toBe('');
	expect(formatDateTime('junk', '')).toBe('');
});

it('formatDateTime adds the reader’s clock time, and invents none for a date-only value', () => {
	const at = new Date(nearMidnight);
	const time = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
	expect(formatDateTime(nearMidnight)).toBe(`${localDay} Aug 2026, ${time}`);
	expect(formatDateTime(' 2026-12-31 ')).toBe('31 Dec 2026');
});
