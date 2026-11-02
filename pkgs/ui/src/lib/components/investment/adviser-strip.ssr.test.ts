import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { adviser as demoAdviser } from '../../fixtures/demo.js';
import AdviserStrip from './adviser-strip.svelte';

/**
 * SSR pins for the strip adviser look: the slim hairline band, the three actions with their
 * permanent aria-labels (the visible labels shed by container width), and initials parity
 * with the other adviser looks.
 */

describe('AdviserStrip (SSR)', () => {
	const body = () => render(AdviserStrip, { props: { adviser: demoAdviser } }).body;

	it('renders the slim hairline band, not a card', () => {
		const b = body();
		expect(b).toContain('border-y');
		expect(b).not.toContain('glass-panel');
		expect(b).not.toContain('data-slot="card"');
	});

	it('derives the initials disc (Eleanor Hughes → EH)', () => {
		expect(body()).toContain('EH');
	});

	it('carries all three actions with the adviser-card hrefs', () => {
		const b = body();
		expect(b).toContain(`href="mailto:${demoAdviser.email}"`);
		expect(b).toContain(`href="tel:${demoAdviser.phone}"`);
		expect(b).toContain('href="/messages"');
	});

	it('keeps aria-labels on the actions — the visible labels collapse in a narrow container', () => {
		const b = body();
		expect(b).toContain(`aria-label="Email ${demoAdviser.name}"`);
		expect(b).toContain(`aria-label="Call ${demoAdviser.name}"`);
		expect(b).toContain(`aria-label="Message ${demoAdviser.name}"`);
		// The label text sheds by CONTAINER width, never viewport.
		expect(b).toContain('@container');
		expect(b).toContain('@md:inline');
		expect(b).not.toMatch(/[^@a-z-]sm:/);
	});

	it('stands the FIRM in when nobody is assigned (kind: firm)', () => {
		const b = render(AdviserStrip, {
			props: {
				adviser: {
					kind: 'firm',
					name: 'Ashworth Wealth',
					firm: 'Ashworth Wealth',
					email: '',
					phone: '0117 555 0000'
				}
			}
		}).body;
		expect(b).toContain('lucide-building-2');
		expect(b).not.toMatch(/>\s*AW\s*</);
		expect(b).toContain('Client support');
		expect(b.match(/Ashworth Wealth/g)).toHaveLength(3); // name + Call/Message aria-labels
		expect(b).not.toContain('href="mailto:');
		expect(b).toContain('href="tel:0117 555 0000"');
		expect(b).toContain('href="/messages"');
	});
});
