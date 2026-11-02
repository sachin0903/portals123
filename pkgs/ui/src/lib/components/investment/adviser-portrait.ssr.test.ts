import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { adviser as demoAdviser } from '../../fixtures/demo.js';
import AdviserPortrait from './adviser-portrait.svelte';
import type { Adviser } from './types.js';

/**
 * SSR pins for the portrait adviser look: initials derivation, the concierge's visible
 * contact details (not hidden inside buttons), and the display-face name.
 */

const count = (body: string, needle: string): number => body.split(needle).length - 1;

describe('AdviserPortrait (SSR)', () => {
	it('derives the initials disc from the name (Eleanor Hughes → EH)', () => {
		const body = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		expect(body).toContain('EH');
		expect(body).toContain('bg-brand-subtle');
	});

	it('a single-word name still yields an initial rather than an empty disc', () => {
		const solo: Adviser = { ...demoAdviser, name: 'Cher' };
		const body = render(AdviserPortrait, { props: { adviser: solo } }).body;
		expect(body).toContain('Cher');
		expect(body).toMatch(/>\s*C\s*</);
	});

	it('SHOWS the email address and phone number as visible text beside the links', () => {
		const body = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		// Once inside the mailto:/tel: href, once as readable text — the review's complaint
		// about adviser-card was exactly that the values only existed inside the hrefs.
		expect(count(body, demoAdviser.email)).toBeGreaterThanOrEqual(2);
		expect(count(body, demoAdviser.phone)).toBeGreaterThanOrEqual(2);
		expect(body).toContain(`href="mailto:${demoAdviser.email}"`);
		expect(body).toContain(`href="tel:${demoAdviser.phone}"`);
		expect(body).toContain('href="/messages"');
	});

	it('sets the name in the display face and the firm as small-caps', () => {
		const body = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		expect(body).toContain('var(--font-display, var(--font-sans))');
		expect(body).toContain(demoAdviser.firm);
		expect(body).toContain('uppercase');
	});

	it('is card-less — no panel chrome', () => {
		const body = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		expect(body).not.toContain('glass-panel');
		expect(body).not.toContain('data-slot="card"');
	});
});

describe('AdviserPortrait — the firm profile (0118)', () => {
	/**
	 * Until 0118, all three adviser looks drew an initials disc and a name — not a design
	 * choice, but the only thing the schema could supply: `auth.users` carried an email, a name, a
	 * mobile and WorkOS's own `avatar`, and nothing a FIRM says about the person.
	 *
	 * Every field is OPTIONAL and every one of these tests has a negative half, because the
	 * failure mode is the same for all of them: a firm that has not filled a field in must get
	 * exactly the card it got before, not an empty row under the adviser's name.
	 */
	const full: Adviser = {
		...demoAdviser,
		jobTitle: 'Chartered Financial Planner',
		qualifications: ['DipPFS', 'Chartered FCSI'],
		bio: 'Eleanor has advised on retirement income for eighteen years.',
		photoUrl: 'https://example.test/eleanor.jpg'
	};

	it('shows the job title between the name and the firm', () => {
		const body = render(AdviserPortrait, { props: { adviser: full } }).body;
		expect(body).toContain('Chartered Financial Planner');
		// Who, then what they do, then where — the order it is read in.
		expect(body.indexOf(full.name)).toBeLessThan(body.indexOf('Chartered Financial Planner'));
		expect(body.indexOf('Chartered Financial Planner')).toBeLessThan(body.indexOf(full.firm));
	});

	it('renders each qualification as its own designation, not a joined sentence', () => {
		const body = render(AdviserPortrait, { props: { adviser: full } }).body;
		// Two credentials are two things. A comma-joined string reads as one.
		expect(count(body, '<li')).toBe(2);
		expect(body).toContain('DipPFS');
		expect(body).toContain('Chartered FCSI');
		expect(body).toContain('aria-label="Qualifications"');
	});

	it('uses the photo when there is one, and the initials when there is not', () => {
		const withPhoto = render(AdviserPortrait, { props: { adviser: full } }).body;
		expect(withPhoto).toContain('src="https://example.test/eleanor.jpg"');
		// alt is EMPTY on purpose: the name is read from the heading immediately below, and a
		// screen reader announcing "photo of Eleanor Hughes" then "Eleanor Hughes" says it twice.
		expect(withPhoto).toContain('alt=""');

		const without = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		expect(without).not.toContain('<img');
		expect(without).toContain('EH');
	});

	it('draws nothing at all for the fields a firm has not filled in', () => {
		const body = render(AdviserPortrait, { props: { adviser: demoAdviser } }).body;
		expect(body).not.toContain('<li');
		expect(body).not.toContain('aria-label="Qualifications"');
		// The card a firm that has configured nothing sees is exactly the card it saw before.
		expect(body).toContain(demoAdviser.name);
		expect(body).toContain(demoAdviser.firm);
	});

	it('an empty qualifications array is the same as none', () => {
		const body = render(AdviserPortrait, {
			props: { adviser: { ...demoAdviser, qualifications: [] } }
		}).body;
		expect(body).not.toContain('aria-label="Qualifications"');
	});
});

describe('AdviserPortrait — the firm standing in (kind: firm)', () => {
	const firm: Adviser = {
		kind: 'firm',
		name: 'Ashworth Wealth',
		firm: 'Ashworth Wealth',
		email: 'help@ashworth.test',
		phone: ''
	};

	it('carries no heading, draws a building rather than initials, and no firm line', () => {
		const body = render(AdviserPortrait, { props: { adviser: firm } }).body;
		expect(body).not.toContain('Your firm');
		expect(body).not.toContain('Your adviser');
		expect(body).toContain('lucide-building-2');
		expect(body).not.toMatch(/>\s*AW\s*</);
		expect(body).toContain('Client support');
		expect(count(body, 'Ashworth Wealth')).toBe(1); // the name only — a firm has no firm
	});

	it('shows only the contacts the firm has given', () => {
		const body = render(AdviserPortrait, { props: { adviser: firm } }).body;
		expect(body).toContain('href="mailto:help@ashworth.test"');
		expect(body).not.toContain('href="tel:');
		expect(body).toContain('href="/messages"');
	});

	it('contains the firm icon rather than cropping it', () => {
		const body = render(AdviserPortrait, {
			props: { adviser: { ...firm, photoUrl: 'https://cdn.test/ashworth.png' } }
		}).body;
		expect(body).toContain('object-contain');
		expect(body).not.toContain('object-cover');
	});
});
