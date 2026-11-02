/**
 * The documents surfaces' real display layer (book/documents/display.ts).
 *
 * `relativeTime` used to live in the platform's `$lib/demo` and measure against `DEMO_NOW`
 * (a hardcoded 2026-06-22). Anything real rendered through it was therefore off by however far
 * the true date had drifted from that fixture clock — plausible-looking and silently wrong. The
 * first block below fails against that version.
 */

import { describe, expect, it } from 'vitest';
import {
	ACTION_META,
	formatDateShort,
	initials,
	relativeTime,
	STATUS_META,
	TONE_CLASSES,
	type DocStatus
} from './display.js';

const DAY_MS = 86_400_000;
const agoMinutes = (n: number): string => new Date(Date.now() - n * 60_000).toISOString();

describe('relative time is measured against the real clock', () => {
	it('reads from now, not from a fixture date', () => {
		expect(relativeTime(agoMinutes(0))).toBe('just now');
		expect(relativeTime(agoMinutes(5))).toBe('5m ago');
		expect(relativeTime(agoMinutes(180))).toBe('3h ago');
		expect(relativeTime(new Date(Date.now() - DAY_MS).toISOString())).toBe('yesterday');
		expect(relativeTime(new Date(Date.now() - 4 * DAY_MS).toISOString())).toBe('4d ago');
	});

	it('accepts an injected clock, so a test never depends on the day it runs', () => {
		const now = new Date('2026-07-28T12:00:00Z');
		expect(relativeTime('2026-07-28T11:30:00Z', now)).toBe('30m ago');
		expect(relativeTime('2026-07-20T12:00:00Z', now)).toBe('8d ago');
	});
});

describe('document chrome', () => {
	it('gives every document status a label and a tone', () => {
		const statuses: DocStatus[] = [
			'queued',
			'generating',
			'active',
			'pending_action',
			'failed',
			'superseded',
			'archived'
		];
		for (const status of statuses) {
			expect(STATUS_META[status].label).toBeTruthy();
			expect(STATUS_META[status].tone).toBeTruthy();
		}
	});

	it('reads `archived` as DELETED — the enum keeps its word, the product does not', () => {
		// The DB/API state is still `archived` (no enum rename), but every surface that shows it now
		// says Deleted, because that is what the control the user pressed said. A badge reading
		// "Archived" beside a "Delete document" button is the soft delete misreading as a filing.
		expect(STATUS_META.archived.label).toBe('Deleted');
		expect(STATUS_META.archived.label).not.toBe('Archived');
	});

	it('gives every event action an icon and a tone that exists in TONE_CLASSES', () => {
		for (const [action, meta] of Object.entries(ACTION_META)) {
			expect(meta.icon, action).toBeTruthy();
			expect(TONE_CLASSES[meta.tone], action).toBeTruthy();
		}
	});
});

describe('formatters', () => {
	it('renders a dense date without the year', () => {
		expect(formatDateShort('2026-06-22T13:04:00Z')).toBe('22 Jun');
	});

	it('takes two initials from a name', () => {
		expect(initials('Margaret Cole')).toBe('MC');
		expect(initials('Ashworth Wealth Management')).toBe('AW');
	});
});
