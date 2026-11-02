import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import CurrencyBadge from './currency-badge.svelte';

/**
 * SSR pins for the currency pill: the code is always printed, the flag is an `alt=""` disc on
 * the left that uses the Badge's leading-icon hook, and a code with no country keeps a neutral
 * disc in that slot so a column of pills lines up.
 */
describe('CurrencyBadge (SSR)', () => {
	it('prints the code with the country flag as a leading disc', () => {
		const { body } = render(CurrencyBadge, { props: { code: 'GBP' } });
		expect(body).toContain('GBP');
		expect(body).toMatch(/<img[^>]*src="[^"]*\/gb\.svg[^"]*"/);
		expect(body).toMatch(/<img[^>]*data-icon="inline-start"/);
		expect(body).toMatch(/<img[^>]*rounded-full/);
		expect(body).toMatch(/<img[^>]*alt=""/);
		expect(body).toMatch(/<img[^>]*aria-hidden="true"/);
	});

	it('is the outline Badge, not a new pill', () => {
		const { body } = render(CurrencyBadge, { props: { code: 'USD' } });
		expect(body).toContain('data-slot="badge"');
		expect(body).toContain('border-border');
		expect(body).toContain('font-normal');
	});

	it('normalises the code it prints and looks up', () => {
		const { body } = render(CurrencyBadge, { props: { code: ' eur ' } });
		expect(body).toContain('EUR');
		expect(body).toMatch(/\/eu\.svg/);
	});

	it('keeps a neutral disc for a code with no country', () => {
		const { body } = render(CurrencyBadge, { props: { code: 'XCD' } });
		expect(body).toContain('XCD');
		expect(body).not.toContain('<img');
		expect(body).toMatch(/<span[^>]*data-icon="inline-start"[^>]*bg-muted/);
	});

	it('passes class and attributes through to the badge', () => {
		const { body } = render(CurrencyBadge, {
			props: { code: 'CHF', class: 'shrink-0', title: 'Swiss franc' }
		});
		expect(body).toContain('shrink-0');
		expect(body).toContain('title="Swiss franc"');
	});
});
