import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import MessageBanner from './message-banner.svelte';

/**
 * SSR pins for the banner message look: the notice band's brand wash + hairline, the same
 * empty-behaviour as message-card, and the plain-text boundary.
 */

describe('MessageBanner (SSR)', () => {
	it('renders nothing when both fields are empty', () => {
		const body = render(MessageBanner, { props: {} }).body;
		expect(body).not.toContain('bg-brand-subtle');
	});

	it('treats whitespace-only copy as empty', () => {
		const body = render(MessageBanner, { props: { title: ' ', body: '\n' } }).body;
		expect(body).not.toContain('bg-brand-subtle');
	});

	it('renders the tinted band with hairline border, glyph, semibold title and body', () => {
		const body = render(MessageBanner, {
			props: { title: 'Office closure', body: 'We are closed on 25 December.' }
		}).body;
		expect(body).toContain('bg-brand-subtle');
		expect(body).toContain('border-brand-line');
		expect(body).toContain('font-semibold');
		expect(body).toContain('Office closure');
		expect(body).toContain('We are closed on 25 December.');
	});

	it('renders title-only and body-only notices', () => {
		expect(render(MessageBanner, { props: { title: 'Deadline' } }).body).toContain('Deadline');
		expect(render(MessageBanner, { props: { body: 'ISA year ends 5 April.' } }).body).toContain(
			'ISA year ends 5 April.'
		);
	});

	it('escapes firm-supplied markup — plain text is the contract', () => {
		const body = render(MessageBanner, { props: { body: '<img src=x onerror=alert(1)>' } }).body;
		expect(body).toContain('&lt;img');
		expect(body).not.toContain('<img');
	});
});
