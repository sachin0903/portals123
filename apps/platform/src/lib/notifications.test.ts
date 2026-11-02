/**
 * `isOnScreen` — whether the reader is already looking at what a notification is about.
 *
 * The whole feature turns on this returning TRUE at the right moment, and every way it can silently
 * return false looks identical from the outside: the toast still pops, the badge still counts, and
 * nothing says the suppression was even attempted. So the type trap is pinned directly — a
 * notification carries `subject_id` as a STRING and the reading register holds a number, so a `===`
 * between them is false for every notification ever sent.
 */
import { describe, expect, it } from 'vitest';
import { isOnScreen } from './notifications';
import { readingContact } from './reading.svelte';

const item = (over: Partial<Parameters<typeof isOnScreen>[0]> = {}) => ({
	category: 'messages',
	subject_type: 'contact' as string | null,
	subject_id: '42' as string | null,
	...over
});

describe('isOnScreen', () => {
	it('is false when nothing is being read', () => {
		expect(isOnScreen(item())).toBe(false);
	});

	it('is true for the contact whose thread is open', () => {
		const done = readingContact(42);
		expect(isOnScreen(item())).toBe(true);
		done();
	});

	it('is false again once the thread is closed', () => {
		readingContact(42)();
		expect(isOnScreen(item())).toBe(false);
	});

	it('is false for a DIFFERENT client’s message', () => {
		const done = readingContact(42);
		expect(isOnScreen(item({ subject_id: '99' }))).toBe(false);
		done();
	});

	it('only ever suppresses messages', () => {
		// Widening this would mean suppressing a notification because some list happens to contain
		// the row, which is not the same as having read it.
		const done = readingContact(42);
		expect(isOnScreen(item({ category: 'tasks' }))).toBe(false);
		expect(isOnScreen(item({ category: 'documents' }))).toBe(false);
		done();
	});

	it('refuses a subject that is not a contact, or is missing', () => {
		const done = readingContact(42);
		expect(isOnScreen(item({ subject_type: 'account' }))).toBe(false);
		expect(isOnScreen(item({ subject_type: null }))).toBe(false);
		expect(isOnScreen(item({ subject_id: null }))).toBe(false);
		expect(isOnScreen(item({ subject_id: 'not-a-number' }))).toBe(false);
		done();
	});

	it('hands the claim over rather than stacking it', () => {
		// Switching conversations registers the new one; the old one must stop matching, or the
		// previous client's notifications keep being swallowed from a page that no longer shows them.
		readingContact(42);
		readingContact(99);
		expect(isOnScreen(item({ subject_id: '42' }))).toBe(false);
		expect(isOnScreen(item({ subject_id: '99' }))).toBe(true);
		readingContact(99)();
	});

	it('a stale cleanup does not clear a newer claim', () => {
		const staleDone = readingContact(42);
		readingContact(99);
		staleDone();
		expect(isOnScreen(item({ subject_id: '99' }))).toBe(true);
		readingContact(99)();
	});
});
