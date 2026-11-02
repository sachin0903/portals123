import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import MessageNote from './message-note.svelte';

/**
 * SSR pins for the note message look (the letter), plus the static safety tripwire for ALL
 * the per-widget look components added alongside it: no {@html} (Svelte's escaping is the
 * boundary for firm-supplied copy) and no raw colours (tokens only, so every look sits on
 * both the card and ledger finishes).
 */

describe('MessageNote (SSR)', () => {
	it('renders nothing when both fields are empty', () => {
		const body = render(MessageNote, { props: {} }).body;
		expect(body).not.toContain('border-s-[3px]');
	});

	it('treats whitespace-only copy as empty', () => {
		const body = render(MessageNote, { props: { title: '  ', body: '\n\t' } }).body;
		expect(body).not.toContain('border-s-[3px]');
	});

	it('renders the brand rule, the display-face title and the prose-measure body', () => {
		const body = render(MessageNote, {
			props: { title: 'A note from your adviser', body: 'Welcome to the portal.' }
		}).body;
		expect(body).toContain('border-s-[3px]');
		expect(body).toContain('var(--font-display, var(--font-sans))');
		expect(body).toContain('max-w-prose');
		expect(body).toContain('whitespace-pre-line');
		expect(body).toContain('A note from your adviser');
		expect(body).toContain('Welcome to the portal.');
	});

	it('renders title-only and body-only notes', () => {
		expect(render(MessageNote, { props: { title: 'Only a title' } }).body).toContain(
			'Only a title'
		);
		expect(render(MessageNote, { props: { body: 'Only a body' } }).body).toContain('Only a body');
	});

	it('escapes firm-supplied markup — plain text is the contract', () => {
		const body = render(MessageNote, {
			props: { title: '<b>bold</b>', body: '<script>alert(1)</script>' }
		}).body;
		// Svelte's SSR escaper rewrites `<` (and `&`) in text — enough to keep any tag inert.
		expect(body).toContain('&lt;b>bold');
		expect(body).toContain('&lt;script>');
		expect(body).not.toContain('<b>bold</b>');
		expect(body).not.toContain('<script>alert(1)');
	});
});

describe('the per-widget looks: static safety tripwire', () => {
	// Every look component this change adds. Kept as an explicit list (not a glob) so a new
	// look must consciously enrol — and a rename can't silently drop one from the scan.
	const LOOKS = [
		'action-items-banner.svelte',
		'activity-timeline.svelte',
		'adviser-portrait.svelte',
		'adviser-strip.svelte',
		'documents-index.svelte',
		'documents-shelf.svelte',
		'message-banner.svelte',
		'message-note.svelte',
		'onboarding-slim.svelte'
	];
	const here = dirname(fileURLToPath(import.meta.url));
	const sources = LOOKS.map((f) => [f, readFileSync(join(here, f), 'utf8')] as const);

	it('no {@html} in any look component — Svelte escaping is the boundary', () => {
		for (const [file, src] of sources) {
			expect(src, file).not.toContain('{@html');
		}
	});

	it('no raw colours in any look component — tokens only', () => {
		for (const [file, src] of sources) {
			expect(src, file).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
			expect(src, file).not.toMatch(/\b(?:rgb|hsl|oklch|oklab)\(/);
		}
	});
});
