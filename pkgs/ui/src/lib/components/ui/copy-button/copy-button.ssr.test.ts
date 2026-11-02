/**
 * The shared copy control.
 *
 * It exists because the same twelve lines had been written four times and had already drifted into
 * three behaviours, so what is worth pinning is the part each hand-rolled copy got wrong: the
 * accessible label must name WHAT is copied (four buttons labelled "Copy" down one definition list
 * tell a screen-reader user nothing), and a refused clipboard must be a quiet no-op rather than an
 * escaping rejection — the clipboard needs a secure context, so it is simply absent over plain http.
 *
 * SSR-rendered, like this package's other component tests: vitest runs in a plain node environment
 * here, so `render` from `svelte/server` is what can see the markup.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import CopyButton from './copy-button.svelte';

describe('CopyButton', () => {
	it('names what it copies, in the label and the tooltip', () => {
		const { body } = render(CopyButton, {
			props: { value: 'doc_69063cf7ca1b44688b8b5987e0f2a1c4', label: 'Copy document id' }
		});
		expect(body).toContain('aria-label="Copy document id"');
		expect(body).toContain('title="Copy document id"');
	});

	it('never renders the value it would copy', () => {
		// It is an affordance ON a value shown elsewhere, not a second copy of it — a key or an id
		// duplicated into a `title`/`value` attribute is one more place it can leak into a screenshot.
		const { body } = render(CopyButton, { props: { value: 'sk_live_supersecret' } });
		expect(body).not.toContain('sk_live_supersecret');
	});

	it('starts un-copied (the tick is a response, not a state)', () => {
		const { body } = render(CopyButton, { props: { value: 'x' } });
		expect(body).toContain('lucide-copy');
		expect(body).not.toContain('lucide-check');
	});

	it('is a pointer target — a text row must not keep the I-beam on hover', () => {
		const { body } = render(CopyButton, { props: { value: 'x', label: 'Copy email' } });
		expect(body).toContain('cursor-pointer');
	});

	it('can hide the glyph until hover, without shifting the row when it appears', () => {
		// `invisible`, not `hidden`: a `hidden` glyph has no box at all, so revealing it on hover grows
		// the button and shifts whatever sits after it in a wrapping row (the profile hero's copyable
		// chips). `invisible` keeps the box reserved the whole time — only its paint toggles.
		const source = readFileSync(join(import.meta.dirname, 'copy-button.svelte'), 'utf8');
		expect(source).toContain("reveal === 'hover'");
		expect(source).toContain('group-hover/button:visible');
		expect(source).toContain('group-focus-visible/button:visible');
		expect(source).not.toMatch(/\bhidden\b.*group-hover\/button:block/);
		const { body } = render(CopyButton, {
			props: { value: 'x', label: 'Copy email', reveal: 'hover' }
		});
		expect(body).toContain('invisible');
		expect(body).toContain('lucide-copy');
	});

	it('swallows a refused clipboard rather than letting it escape', () => {
		// A copy affordance is a decoration on someone else's page: an unhandled rejection from it must
		// never be able to break that page. Pinned on the source because a rejected `writeText` cannot be
		// driven in this environment (no DOM, no clipboard, no user gesture).
		const source = readFileSync(join(import.meta.dirname, 'copy-button.svelte'), 'utf8');
		expect(source).toMatch(/await navigator\.clipboard\.writeText\(value\);/);
		expect(source).toMatch(/\} catch \{/);
		// …and the failure branch does nothing at all — no rethrow, no toast this component cannot own.
		const tail = source.slice(source.indexOf('} catch {'));
		expect(tail.slice(0, tail.indexOf('}'))).not.toMatch(/throw|toast/);
	});
});
