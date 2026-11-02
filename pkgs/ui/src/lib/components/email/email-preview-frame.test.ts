import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EmailPreviewFrame from './email-preview-frame.svelte';

/**
 * The security posture of the email preview, pinned two ways.
 *
 * An email body is author-controlled HTML that the API deliberately does not sanitise — it merges
 * tokens and inlines CSS, nothing more. `sandbox=""` is the whole of what stops that markup running
 * script in the app's own origin, under a signed-in staff session. So it is pinned by RENDER (the
 * attribute the browser will actually receive) and by SOURCE TEXT (the same static-guard technique
 * as branding/preview-frame.test.ts and apps/client's aml-leak.test.ts), because the render check
 * alone would keep passing if someone added a second, laxer iframe to the file.
 *
 * If a change here fails: the fix is never to relax the sandbox. Email clients do not run script.
 */
const here = import.meta.dirname;
const source = readFileSync(join(here, 'email-preview-frame.svelte'), 'utf8');

/** Strip comments — HTML, block AND `//` line comments (the `:` lookbehind spares `https://`) — so
 * prose explaining the hazard cannot satisfy (or trip) a match. */
const stripComments = (code: string) =>
	code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');
const markup = stripComments(source);

const html = '<p>Hello <script>alert(1)</script></p>';

describe('the frame is sandboxed with nothing allowed', () => {
	it('renders an iframe carrying an EMPTY sandbox attribute', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 'Email preview' } });
		expect(body).toContain('<iframe');
		// The empty value is the point: `sandbox` with no tokens = every restriction on.
		// (`[\s\S]` not `[^>]`: the srcdoc value legitimately carries `>` — Svelte escapes `<` to
		// `&lt;` on the way into the attribute but leaves `>` alone.)
		expect(body).toMatch(/<iframe[\s\S]*?\ssandbox=""[\s\S]*?><\/iframe>/);
	});

	it('grants no sandbox token at all — above all not scripts or same-origin', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 'Email preview' } });
		expect(body).not.toContain('allow-scripts');
		expect(body).not.toContain('allow-same-origin');
		// Nothing else either: no forms, popups, top-navigation or downloads on a preview.
		expect(body).not.toMatch(/sandbox="[^"]+"/);
	});

	it('never renders the body outside the frame — srcdoc is the only sink', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 'Email preview' } });
		// A stray {@html …} would execute the same markup in the app's own document, sandbox or not.
		expect(markup).not.toContain('@html');
		expect(body).not.toContain('<script>alert(1)</script>');
	});

	it('has exactly one iframe in the source, and it is the sandboxed one', () => {
		// A second frame is how the guarantee gets bypassed without any test noticing.
		expect(markup.match(/<iframe/g)).toHaveLength(1);
		expect(markup).toMatch(/<iframe[\s\S]*?sandbox=""[\s\S]*?><\/iframe>/);
		// `sandbox` must be a literal, not a prop/expression a call site could widen.
		expect(markup).not.toMatch(/sandbox=\{/);
		expect(markup).not.toContain('allow-scripts');
		expect(markup).not.toContain('allow-same-origin');
	});
});

describe('the accessible name is not optional', () => {
	it('puts the required title on the iframe', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 'Welcome email' } });
		expect(body).toContain('title="Welcome email"');
	});
});

describe('geometry', () => {
	it('frames at the requested device width, full size by default', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 't', width: 375 } });
		expect(body).toContain('width: 375px');
		// scale 1 ⇒ no transform, and the frame stays interactive (scrollable).
		expect(body).not.toContain('transform: scale');
		expect(body).not.toContain('pointer-events: none');
	});

	it('a sub-1 scale reads as a thumbnail: top-left transform, not interactive', () => {
		const { body } = render(EmailPreviewFrame, {
			props: { html, title: 't', width: 640, scale: 0.25 }
		});
		expect(body).toContain('transform: scale(0.25)');
		expect(body).toContain('transform-origin: top left');
		expect(body).toContain('pointer-events: none');
		// The host reserves the SCALED width so the page's layout is stable…
		expect(body).toContain('width: 160px');
		// …while the iframe's own box is 1/scale of it, keeping the internal viewport at 640px.
		expect(body).toContain('width: 400%');
		expect(body).toContain('height: 400%');
	});

	it('merges a caller class onto the framed box', () => {
		const { body } = render(EmailPreviewFrame, {
			props: { html, title: 't', class: 'h-[60vh] rounded-lg border bg-white shadow-sm' }
		});
		expect(body).toContain('h-[60vh]');
		expect(body).toContain('rounded-lg');
		// The box clips the scaled frame; a caller must not be able to drop that.
		expect(body).toContain('overflow-hidden');
	});

	it('fills the parent when asked, so a fixed pane can scroll the email inside', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 't', fill: true } });
		expect(body).toContain('h-full');
		expect(body).not.toContain('width: 640px');
	});

	it('puts a fragment in standards mode so a filled pane cannot stretch the card', () => {
		const { body } = render(EmailPreviewFrame, { props: { html, title: 't', fill: true } });
		expect(body).toMatch(/DOCTYPE html/i);
	});
});
