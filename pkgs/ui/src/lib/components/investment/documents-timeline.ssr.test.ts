import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DocumentsTimeline from './documents-timeline.svelte';
import type { ClientDocument } from './types.js';

/**
 * SSR pins for the client's default documents look: the Files-app recency groups ("This
 * month", "Last month", "Earlier in <year>", then one per earlier year), the stamp that carries
 * the year only once the group no longer says it, the undated run, and the unread emphasis.
 */

const doc = (id: string, isoDate: string | undefined, unread = false): ClientDocument => ({
	id,
	name: `Document ${id}`,
	category: 'Statements',
	date: 'some day',
	isoDate,
	size: '1 KB',
	format: 'PDF',
	unread
});

// "Today" is 1 October 2026 — so September is last month, and anything from January to
// August files under "Earlier in 2026".
const now = new Date('2026-10-01T09:00:00Z');

const headings = (body: string): string[] =>
	[...body.matchAll(/<h2[^>]*>\s*([^<]+?)\s*<\/h2>/g)].map((m) => m[1]!);

describe('DocumentsTimeline (SSR)', () => {
	it('groups by recency, newest first, never by calendar month', () => {
		const body = render(DocumentsTimeline, {
			props: {
				now,
				documents: [
					doc('a', '2025-03-12'),
					doc('b', '2026-09-28'),
					doc('c', '2026-10-01'),
					doc('d', '2026-04-02'),
					doc('e', '2026-01-15'),
					doc('f', '2024-11-03')
				]
			}
		}).body;
		expect(headings(body)).toEqual(['This month', 'Last month', 'Earlier in 2026', '2025', '2024']);
		expect(body).not.toContain('September 2026');
		expect(body).not.toContain('January 2026');
	});

	it('a group that would be empty is simply absent', () => {
		const body = render(DocumentsTimeline, {
			props: { now, documents: [doc('a', '2026-09-28'), doc('b', '2025-06-04')] }
		}).body;
		expect(headings(body)).toEqual(['Last month', '2025']);
	});

	it('the row stamp carries the year only in the per-year groups', () => {
		const body = render(DocumentsTimeline, {
			props: { now, documents: [doc('a', '2026-04-02'), doc('b', '2025-06-04')] }
		}).body;
		expect(body).toContain('Statements · 2 Apr<');
		expect(body).toContain('Statements · 4 Jun 2025<');
	});

	it('undated documents file in one unnamed run at the end, never dropped', () => {
		const body = render(DocumentsTimeline, {
			props: { now, documents: [doc('a', undefined), doc('b', '2026-09-28')] }
		}).body;
		expect(headings(body)).toEqual(['Last month']);
		expect(body).toContain('Document a');
		// The loader's display date stands in for the stamp.
		expect(body).toContain('Statements · some day<');
	});

	it('an unread row is marked for the eye and for the screen reader', () => {
		const body = render(DocumentsTimeline, {
			props: { now, documents: [doc('a', '2026-09-28', true), doc('b', '2026-09-15', false)] }
		}).body;
		expect(body.split('(new)').length - 1).toBe(1);
		expect(body.split('bg-brand-subtle text-brand-strong').length - 1).toBe(1);
		expect(body.split('leading-snug font-semibold').length - 1).toBe(1);
	});
});
