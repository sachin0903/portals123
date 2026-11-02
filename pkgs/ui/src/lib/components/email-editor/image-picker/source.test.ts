import { describe, expect, it } from 'vitest';
import {
	checkPastedLink,
	classifyImageSource,
	describeImageSource,
	fileNameOf,
	isMergeSource,
	isUsableInEmail
} from './source.js';

/**
 * The one question this module answers: will a recipient's mail client draw this image? It fetches
 * the URL itself, unauthenticated, whenever the mail is opened — so an absolute https URL (or a
 * merge token that becomes one) is the whole of what works, and the two sources that look right on
 * the canvas and arrive blank have to be named as such rather than passed through.
 */
describe('what an email image source is', () => {
	it.each([
		['', 'none'],
		['   ', 'none'],
		['https://cdn.example/logo.png', 'hosted'],
		['HTTPS://cdn.example/logo.png', 'hosted'],
		['http://cdn.example/logo.png', 'insecure'],
		['data:image/png;base64,AAAA', 'embedded'],
		['DATA:image/png;base64,AAAA', 'embedded'],
		['/files/tenant/logo.png', 'relative'],
		['logo.png', 'relative'],
		['{{ logo_url }}', 'merge'],
		// THE FIRM'S OWN LIBRARY. A relative path, and the one that does arrive: the send path puts
		// the firm's host in front of it (`app/email/document.py::to_email_html`).
		['/_assets/logo-0123456789abcdef.png', 'relay'],
		['/_assets/logo-dark-0123456789abcdef.webp', 'relay'],
		// …but only the relay's exact grammar. A short digest, a missing one and a traversal
		// attempt are ordinary relative paths, because `app/storage.py` will not serve them either
		// — calling one usable here would send it and arrive broken.
		['/_assets/logo-9f2c.png', 'relative'],
		['/_assets/logo.png', 'relative'],
		['/_assets/../secret-0123456789abcdef.png', 'relative'],
		['/_assets/logo-0123456789ABCDEF.png', 'relative']
	] as const)('classifies %s as %s', (src, kind) => {
		expect(classifyImageSource(src)).toBe(kind);
	});

	it('treats a library path as usable, and says only which picture it is', () => {
		// The author does not need to know how their firm's images are served. What they must not be
		// told is that their own uploaded logo "resolves only in this editor", which is what the
		// generic relative case says — it was the copy this whole path used to fall into.
		const relay = '/_assets/logo-0123456789abcdef.png';
		expect(isUsableInEmail(relay)).toBe(true);
		expect(describeImageSource(relay)).toBe('logo-0123456789abcdef.png');
		expect(checkPastedLink(relay)).toEqual({ ok: true, message: '' });
	});

	it('still refuses the paths that really do resolve nowhere else', () => {
		expect(isUsableInEmail('/files/tenant/logo.png')).toBe(false);
		expect(describeImageSource('/files/tenant/logo.png')).toMatch(/only in this editor/);
		expect(checkPastedLink('/_assets/logo.png').ok).toBe(false);
	});

	it('reads a token INSIDE a URL as a merge source', () => {
		// The token is what decides at send, whatever surrounds it — and a URL that is partly a token
		// is not a URL this module can otherwise judge.
		expect(classifyImageSource('https://cdn.example/{{ tenant }}/logo.png')).toBe('merge');
		expect(isMergeSource('https://cdn.example/{{ tenant }}/logo.png')).toBe(true);
	});

	it('accepts only what an inbox can fetch', () => {
		expect(isUsableInEmail('https://cdn.example/logo.png')).toBe(true);
		expect(isUsableInEmail('{{ logo_url }}')).toBe(true);
		// A data: URI is the one GrapesJS's own asset manager produces from a local file, and it is
		// exactly what mail clients refuse — `app/email/transport.py::masthead_image` refuses one too.
		expect(isUsableInEmail('data:image/png;base64,AAAA')).toBe(false);
		expect(isUsableInEmail('/files/tenant/logo.png')).toBe(false);
		expect(isUsableInEmail('http://cdn.example/logo.png')).toBe(false);
		expect(isUsableInEmail('')).toBe(false);
	});

	it('says WHY an unusable source will not arrive, and what to do', () => {
		for (const src of ['data:image/png;base64,AAAA', '/logo.png', 'http://x/logo.png']) {
			const said = describeImageSource(src);
			// Every refusal names a fix. "Invalid" tells an author nothing they can act on.
			expect(said.toLowerCase()).toMatch(/https|hosted/);
		}
		expect(describeImageSource('')).toBe('No image chosen.');
		expect(describeImageSource('{{ logo_url }}')).toBe('Filled in at send.');
	});

	it('stamps a working source with its file name rather than the whole URL', () => {
		// Provenance is a compact stamp, not prose — and the rail's hint is one line wide.
		expect(describeImageSource('https://cdn.example/files/tenant/logo-9f2c.png')).toBe(
			'logo-9f2c.png'
		);
	});
});

describe('fileNameOf', () => {
	it.each([
		['https://cdn.example/a/b/logo.png', 'logo.png'],
		['https://cdn.example/a/b/logo.png?v=2', 'logo.png'],
		['https://cdn.example/a/b/logo.png#top', 'logo.png'],
		['https://cdn.example/my%20logo.png', 'my logo.png'],
		// No extension, so the segment names a route rather than a file: `placehold.co/300x90/png`
		// rendered as "png" alone, which identifies nothing.
		['https://cdn.example/a/b/', 'cdn.example/b'],
		['https://placehold.co/300x90/png', 'placehold.co/png'],
		['https://www.cdn.example/logo', 'cdn.example/logo']
	])('%s -> %s', (url, name) => {
		expect(fileNameOf(url)).toBe(name);
	});

	it('falls back to the raw segment on an undecodable escape', () => {
		// A stray `%` throws in decodeURIComponent. The label is not worth an exception.
		expect(fileNameOf('https://cdn.example/100%.png')).toBe('100%.png');
	});
});

describe('a pasted link', () => {
	it('is accepted when an inbox could fetch it', () => {
		expect(checkPastedLink('https://cdn.example/logo.png')).toEqual({ ok: true, message: '' });
		expect(checkPastedLink('{{ logo_url }}')).toEqual({ ok: true, message: '' });
	});

	it('says nothing while the field is still empty', () => {
		// An error under a field nobody has typed in yet is noise, not validation.
		expect(checkPastedLink('')).toEqual({ ok: false, message: '' });
	});

	it('refuses the rest with the reason', () => {
		for (const bad of ['data:image/png;base64,AAAA', 'http://x/logo.png', 'logo.png']) {
			const answer = checkPastedLink(bad);
			expect(answer.ok).toBe(false);
			expect(answer.message).not.toBe('');
		}
	});
});
