import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DocumentViewer from './document-viewer.svelte';

/**
 * Which renderer each content type gets, and what the server sends before any of them run.
 *
 * A PDF is drawn by pdf.js now, not handed to the browser's built-in plugin in an iframe. The
 * plugin brought its own toolbar, grey surround and scrollbar — chrome we don't control, that
 * ignores the theme, and that differs per browser.
 */

const body = (props: Record<string, unknown>) =>
	render(DocumentViewer, { props: { src: '/documents/doc_1/content', ...props } }).body;

describe('DocumentViewer — renderer routing (SSR)', () => {
	it('sends a PDF to the pdf.js viewer, not an iframe', () => {
		const out = body({ contentType: 'application/pdf' });
		expect(out).not.toContain('<iframe');
		// pdf.js is client-only, so the server sends the scroll container and its spinner; the
		// canvases are painted after hydration.
		expect(out).toContain('Rendering preview…');
	});

	it('never emits the old #toolbar=0 iframe URL', () => {
		// The fragment only ever suppressed the plugin's toolbar, and only where the browser chose
		// to honour it. If this string comes back, the iframe came back with it.
		expect(body({ contentType: 'application/pdf' })).not.toContain('#toolbar=0');
	});

	it('keeps HTML in an iframe, where the sandbox is the point', () => {
		expect(body({ contentType: 'text/html' })).toContain('<iframe');
	});

	it('still renders an image as an image', () => {
		const out = body({ contentType: 'image/png', name: 'Scan' });
		expect(out).toContain('<img');
		expect(out).toContain('alt="Scan"');
	});

	it('offers no preview without a source, whatever the type claims', () => {
		const out = render(DocumentViewer, { props: { contentType: 'application/pdf' } }).body;
		expect(out).toContain('No preview available');
		expect(out).not.toContain('<iframe');
	});

	it('says a type it cannot draw is a download, rather than showing an empty frame', () => {
		expect(body({ contentType: 'application/vnd.ms-excel' })).toContain('download to view');
	});
});
