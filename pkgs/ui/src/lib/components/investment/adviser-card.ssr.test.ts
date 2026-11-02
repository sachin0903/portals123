import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { adviser as demoAdviser } from '../../fixtures/demo.js';
import AdviserCard from './adviser-card.svelte';

/**
 * SSR pins for the card adviser look. The home default is this look at half the page, and a
 * stacked identity-then-buttons left the right of that panel blank — the actions share the
 * name's row once the card itself is wide enough (`@md`), and stack under it when it is not.
 */

describe('AdviserCard (SSR)', () => {
	const body = () => render(AdviserCard, { props: { adviser: demoAdviser } }).body;

	it('puts the actions on the name’s row once the card is wide, and stacks them when it is not', () => {
		const b = body();
		expect(b).toContain('@container');
		expect(b).toContain('flex-col');
		expect(b).toContain('@md:flex-row');
		expect(b).toContain('@md:items-center');
	});

	it('derives the initials disc (Eleanor Hughes → EH)', () => {
		expect(body()).toContain('EH');
	});

	it('renders Email and Call as named icons and Message as the one labelled, filled action', () => {
		const b = body();
		expect(b).toContain(`href="mailto:${demoAdviser.email}"`);
		expect(b).toContain(`href="tel:${demoAdviser.phone}"`);
		expect(b).toContain('href="/messages"');
		const email = b.slice(b.indexOf('<a href="mailto:'), b.indexOf('<a href="tel:'));
		const call = b.slice(b.indexOf('<a href="tel:'), b.indexOf('<a href="/messages"'));
		const message = b.slice(b.indexOf('<a href="/messages"'));
		// Icon-only, so the accessible name carries the verb and the person.
		expect(email).toContain(`aria-label="Email ${demoAdviser.name}"`);
		expect(call).toContain(`aria-label="Call ${demoAdviser.name}"`);
		expect(email).not.toMatch(/>\s*Email\s*</);
		expect(call).not.toMatch(/>\s*Call\s*</);
		expect(message).toContain(' Message');
		// Message is the filled action; Email stays an outline button.
		expect(email).not.toContain('bg-primary');
		expect(message).toContain('bg-primary');
	});

	it('gives every action a 44px hit area without growing the 36px controls', () => {
		const b = body();
		const actions = b.slice(b.indexOf('<a href="mailto:'));
		expect(actions.match(/tap-target/g)).toHaveLength(3);
		expect(actions.match(/size-9/g)).toHaveLength(2); // the two icons
		expect(actions).toContain('h-9'); // Message
	});

	describe('the firm standing in (kind: firm)', () => {
		const firm = {
			kind: 'firm' as const,
			name: 'Ashworth Wealth',
			firm: 'Ashworth Wealth',
			email: 'help@ashworth.test',
			phone: '',
			photoUrl: 'https://cdn.test/ashworth.png'
		};
		const firmBody = (over: Partial<typeof firm> = {}) =>
			render(AdviserCard, { props: { adviser: { ...firm, ...over } } }).body;

		it('carries no heading (the logo and name introduce it) and never prints a firm line under the firm', () => {
			const b = firmBody();
			expect(b).not.toContain('Your firm');
			expect(b).not.toContain('Your adviser');
			expect(b).not.toContain('<h2');
			expect(b).toContain('Client support');
			// The name, and the Email icon's aria-label + its hover title — never a firm line.
			expect(b.match(/Ashworth Wealth/g)).toHaveLength(3);
			expect(b).not.toMatch(/truncate text-sm">Ashworth Wealth</);
		});

		it('shows the icon contained rather than cropped, and a building when there is none', () => {
			expect(firmBody()).toContain('object-contain');
			expect(firmBody()).not.toContain('>AW<');
			expect(firmBody({ photoUrl: undefined })).toContain('lucide-building-2');
		});

		it('draws no action for a contact the firm has not given', () => {
			const b = firmBody();
			expect(b).toContain('href="mailto:help@ashworth.test"');
			expect(b).not.toContain('href="tel:');
			expect(b).toContain('href="/messages"');
			expect(firmBody({ email: '' })).not.toContain('href="mailto:');
		});
	});
});
