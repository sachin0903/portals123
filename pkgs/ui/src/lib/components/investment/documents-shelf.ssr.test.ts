import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { documents as demoDocuments } from '../../fixtures/demo.js';
import DocumentsShelf from './documents-shelf.svelte';
import type { ClientDocument, DocumentCategory } from './types.js';

/**
 * SSR pins for the shelf documents look: tile count, the interactive/inert branch, and the
 * unknown-category fallback shared with documents-list.
 */

const count = (body: string, needle: string): number => body.split(needle).length - 1;

const docs = demoDocuments.slice(0, 5);

describe('DocumentsShelf (SSR)', () => {
	it('renders one tile per document', () => {
		const body = render(DocumentsShelf, { props: { documents: docs } }).body;
		expect(count(body, 'data-shelf-tile')).toBe(docs.length);
	});

	it('tiles are links when interactive (the default)', () => {
		const body = render(DocumentsShelf, { props: { documents: docs } }).body;
		expect(count(body, 'href="/documents/')).toBe(docs.length);
	});

	it('interactive={false} renders inert tiles — no anchors at all', () => {
		const body = render(DocumentsShelf, { props: { documents: docs, interactive: false } }).body;
		expect(count(body, 'data-shelf-tile')).toBe(docs.length);
		expect(body).not.toContain('<a');
		// The hover affordance goes with the link — an inert tile must not promise a lift.
		expect(body).not.toContain('hover:border-brand');
	});

	it('an unknown category falls back to the generic glyph instead of throwing', () => {
		// Server drift: a category this build has never heard of must still render its tile.
		const junk: ClientDocument = {
			id: 'mystery-1',
			category: 'Compliance Pack' as DocumentCategory,
			name: 'MiFID Costs Bundle',
			date: '5 Apr 2026',
			size: '412 KB',
			format: 'PDF'
		};
		const body = render(DocumentsShelf, { props: { documents: [junk] } }).body;
		expect(body).toContain('MiFID Costs Bundle');
	});

	it('columns key off the CONTAINER, names clamp to two lines', () => {
		const body = render(DocumentsShelf, { props: { documents: docs } }).body;
		expect(body).toContain('@container');
		expect(body).toContain('@2xl:grid-cols-3');
		expect(body).toContain('@4xl:grid-cols-4');
		expect(body).toContain('line-clamp-2');
	});
});
