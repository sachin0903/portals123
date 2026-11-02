import { describe, expect, it } from 'vitest';
import type { Branding } from '@graphene/auth';
import { adviserOrFirm, firmContact, type Adviser } from './adviser';

/**
 * The firm standing in for an unassigned adviser: its name, icon and support contacts, flagged
 * `kind: 'firm'` so the looks head the block "Your firm" and never print a job title under it.
 */

const branding: Branding = {
	icon_url: 'https://cdn.example/acme-icon.png',
	support: { email: ' help@acme.example ', phone: '0800 123 4567', website: 'https://acme.example' }
};

describe('firmContact', () => {
	it('builds the firm as the contact from the brand name, icon and support details', () => {
		expect(firmContact(branding, 'Acme Wealth')).toEqual({
			kind: 'firm',
			name: 'Acme Wealth',
			firm: 'Acme Wealth',
			email: 'help@acme.example',
			phone: '0800 123 4567',
			photoUrl: 'https://cdn.example/acme-icon.png'
		});
	});

	it('leaves email/phone empty and omits the icon when the firm has set none', () => {
		const firm = firmContact({}, 'Acme Wealth');
		expect(firm.email).toBe('');
		expect(firm.phone).toBe('');
		expect('photoUrl' in firm).toBe(false);
	});

	it('falls back to a plain label when there is no brand at all', () => {
		expect(firmContact(null, null).name).toBe('Your firm');
		expect(firmContact(null, '   ').name).toBe('Your firm');
	});
});

describe('adviserOrFirm', () => {
	const adviser: Adviser = {
		name: 'Eleanor Hughes',
		firm: 'Acme Wealth',
		email: 'e@acme.example',
		phone: '0121'
	};

	it('keeps an assigned adviser untouched', () => {
		expect(adviserOrFirm(adviser, branding, 'Acme Wealth')).toBe(adviser);
	});

	it('substitutes the firm when nobody is assigned', () => {
		expect(adviserOrFirm(null, branding, 'Acme Wealth').kind).toBe('firm');
		expect(adviserOrFirm(undefined, branding, 'Acme Wealth').name).toBe('Acme Wealth');
	});
});
