import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ExportMenu from './export-menu.svelte';

/**
 * SSR pins for the shared export trigger.
 *
 * A closed dropdown renders its trigger and none of its items on the server, so the format links
 * are not observable here — what is, and what these pin, is the trigger itself. That is the part
 * with a history: it shipped icon-only for a while, which lost the word the per-page control it
 * replaced had ("Export CSV"). `title`/`aria-label` covered assistive tech but not the person
 * scanning the card for the first time, so the word is back and pinned.
 */

describe('ExportMenu (SSR)', () => {
	const props = { href: '/clients/312/transactions/export' };

	it('shows the word, not just a glyph', () => {
		const { body } = render(ExportMenu, { props });
		expect(body).toContain('Export');
		// Not `w-9 p-0` — the icon-only shape had no room for a label.
		expect(body).not.toContain('w-9 p-0');
	});

	it('offers Excel and CSV, and not PDF', () => {
		// The generator still answers `format=pdf`. The menu does not: a closed dropdown renders no
		// items, so the offer itself is pinned from the source rather than from the trigger.
		const source = readFileSync(new URL('./export-menu.svelte', import.meta.url), 'utf8');
		expect(source).toContain("format: 'xlsx'");
		expect(source).toContain("format: 'csv'");
		expect(source).not.toContain("format: 'pdf'");
	});

	it('names the action, never one of the formats', () => {
		// "Export CSV" on the trigger would hide the other format behind a button claiming to be
		// the first, which is what this component exists to stop.
		const { body } = render(ExportMenu, { props });
		expect(body).not.toContain('Export CSV');
		expect(body).not.toContain('Export PDF');
		expect(body).not.toContain('Export Excel');
	});

	it('gives the accessible name the same string as the visible one', () => {
		// They come from one prop, so they cannot drift — a mismatch is what breaks speech input,
		// where "click Export" must reach the control that reads Export.
		const { body } = render(ExportMenu, { props: { ...props, label: 'Export' } });
		expect(body).toContain('aria-label="Export"');
	});

	it('takes the caller’s word when one is given', () => {
		const { body } = render(ExportMenu, { props: { ...props, label: 'Download' } });
		expect(body).toContain('aria-label="Download"');
		expect(body).toContain('Download');
	});

	it('is a quiet outline control, not a primary action', () => {
		// It sits at the foot of the card beside the pager. Exporting is not the thing the page is
		// for, so it must not compete with the actions that are.
		const { body } = render(ExportMenu, { props });
		expect(body).not.toContain('bg-primary');
	});
});
