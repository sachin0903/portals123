import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { BrandingBlock } from '@graphene/auth';
import type { PerformancePoint } from '../charts/performance-chart.svelte';
import AllocationDonut from '../charts/allocation-donut.svelte';
import { personaByKey } from '../../fixtures/personas.js';
import PortalPage from './portal-page.svelte';
import PortfolioHeader from './portfolio-header.svelte';
import PortfolioHeaderCanvas from './portfolio-header-canvas.svelte';
import PortfolioHeaderCompact from './portfolio-header-compact.svelte';
import PortfolioHeaderLedger from './portfolio-header-ledger.svelte';
import type { PortalPageData } from './types.js';

/**
 * SSR pins for the two statement looks (canvas/ledger) and the provenance stamps: totals and
 * "as at" render server-side (no hydration required to see money), the `actions` setting is
 * honoured including 'none', and the inert (`interactive: false`) render carries no focus
 * stops or live links. Motion/scrubbing needs a real event loop — that part is browser QA.
 */

const performance: PerformancePoint[] = [
	{ label: '2026-01-29', value: 158_960 },
	{ label: '2026-04-29', value: 165_000 },
	{ label: '2026-07-29', value: 171_320 }
];

const BASE = {
	total: 171_320,
	gain: 12_360,
	gainPct: 0.078,
	performance,
	asOf: '2026-07-29',
	period: { from: '2026-01-29', to: '2026-07-29' }
};

describe('PortfolioHeaderCanvas (SSR)', () => {
	it('renders the full total and the period toggle server-side', () => {
		const { body } = render(PortfolioHeaderCanvas, { props: BASE });
		expect(body).toContain('canvas-surface');
		// AnimatedMoney SSRs the FINAL figure (major + minor split by Money) — no count-up flash.
		expect(body).toContain('£171,320');
		expect(body).toContain('.00');
		// The window is the toggle's pressed pill; the ink headers print no "as at" line or
		// period sentence any more (the ledger statement keeps both).
		expect(body).toMatch(/aria-pressed="true"[^>]*>6M</);
		expect(body).not.toContain('as at 29 Jul 2026');
	});

	it('never fabricates a stamp when as_of is absent', () => {
		const { body } = render(PortfolioHeaderCanvas, {
			props: { ...BASE, asOf: null, period: null }
		});
		expect(body).not.toContain('as at');
		expect(body).not.toContain('to 29 Jul');
	});

	it("actions 'none' renders no action row at all", () => {
		const { body } = render(PortfolioHeaderCanvas, { props: { ...BASE, actions: 'none' } });
		expect(body).not.toContain('>Payment<');
		expect(body).not.toContain('Withdraw');
		expect(body).not.toContain('Contact your adviser');
	});

	it("actions 'contact' renders the one adviser button", () => {
		const { body } = render(PortfolioHeaderCanvas, { props: { ...BASE, actions: 'contact' } });
		expect(body).toContain('Contact your adviser');
		expect(body).toContain('href="/messages"');
		expect(body).not.toContain('>Payment<');
	});

	it('default_period seeds the window', () => {
		const { body } = render(PortfolioHeaderCanvas, { props: { ...BASE, defaultPeriod: '1Y' } });
		expect(body).toMatch(/aria-pressed="true"[^>]*>1Y</);
		expect(body).not.toMatch(/aria-pressed="true"[^>]*>6M</);
	});

	it('show_chart: false keeps the field but drops the luminous path', () => {
		const chartless = render(PortfolioHeaderCanvas, { props: { ...BASE, showChart: false } }).body;
		expect(chartless).toContain('canvas-surface');
		expect(chartless).not.toContain('performance:'); // the chart's aria-label vocabulary
	});

	it('inert render: no live links, no focusable chart, still the final figures', () => {
		const { body } = render(PortfolioHeaderCanvas, { props: { ...BASE, interactive: false } });
		expect(body).toContain('£171,320');
		expect(body).not.toContain('href="/transfer/deposit"');
		expect(body).toContain('>Payment<'); // same pixels, as spans
		expect(body).not.toContain('tabindex="0"');
		// The drift only runs on a live surface.
		expect(body).not.toContain('canvas-live');
	});
});

describe('PortfolioHeaderLedger (SSR)', () => {
	it('renders on the page ground in the display face, with stamp and statement figures', () => {
		const { body } = render(PortfolioHeaderLedger, { props: BASE });
		expect(body).toContain('ledger-header');
		// The display-face token with its exact fallback chain.
		expect(body).toContain('var(--font-display, var(--font-sans))');
		expect(body).toContain('£171,320');
		expect(body).toContain('as at 29 Jul 2026');
		expect(body).toContain('Growth since investment');
		expect(body).toContain('Period high');
		// NO ink card and NO big chart: hero surface and chart vocabulary must be absent.
		expect(body).not.toContain('hero-surface');
		expect(body).not.toContain('performance:');
	});

	it("actions 'none' drops the action row; default keeps Payment / Withdraw", () => {
		expect(
			render(PortfolioHeaderLedger, { props: { ...BASE, actions: 'none' } }).body
		).not.toContain('>Payment<');
		const withDefault = render(PortfolioHeaderLedger, { props: BASE }).body;
		expect(withDefault).toContain('href="/transfer/deposit"');
		expect(withDefault).toContain('href="/transfer/withdraw"');
	});

	it('omits Invested when withdrawals push the basis negative (matches the hero)', () => {
		const { body } = render(PortfolioHeaderLedger, {
			props: { ...BASE, total: 1000, gain: 2000 }
		});
		expect(body).not.toContain('Invested');
		expect(body).toContain('Growth since investment');
	});
});

describe('PortalPage dispatch + provenance threading (SSR)', () => {
	const trust = personaByKey('trust300');
	const stamped = { ...trust.data, as_of: '2026-07-29' };

	function html(blocks: BrandingBlock[], data: PortalPageData = stamped): string {
		return render(PortalPage, { props: { blocks, data, interactive: false } }).body;
	}

	it("variant 'canvas' renders the canvas look", () => {
		const body = html([{ type: 'portfolioHeader', settings: { variant: 'canvas' } }]);
		expect(body).toContain('canvas-surface');
		expect(body).toContain('Portfolio value');
	});

	it("variant 'ledger' renders the ledger look — even with show_chart off (no forced compact)", () => {
		const body = html([
			{ type: 'portfolioHeader', settings: { variant: 'ledger', show_chart: false } }
		]);
		expect(body).toContain('ledger-header');
		expect(body).toContain('as at 29 Jul 2026');
	});

	it('the actions/default_period settings thread through the dispatch', () => {
		const body = html([
			{
				type: 'portfolioHeader',
				settings: { variant: 'canvas', actions: 'contact', default_period: '1Y' }
			}
		]);
		expect(body).toContain('Contact your adviser');
		expect(body).toMatch(/aria-pressed="true"[^>]*>1Y</);
		expect(body).not.toContain('>Payment<');
	});

	it('junk actions/default_period degrade to the historic defaults', () => {
		const body = html([
			{
				type: 'portfolioHeader',
				settings: { variant: 'hero', actions: 42, default_period: '2W' }
			}
		]);
		expect(body).toContain('>Payment<');
		expect(body).toMatch(/aria-pressed="true"[^>]*>6M</);
	});

	it('the accounts and holdings cards stamp their headings when the data carries as_of', () => {
		// The stamp rides the heading beside the title (WidgetHeader) — the cards print no
		// description sentence any more.
		const body = html([
			{ type: 'accounts', settings: { variant: 'table' } },
			{ type: 'holdings', settings: { variant: 'table' } }
		]);
		expect(body.match(/as at 29 Jul 2026/g)).toHaveLength(2);
		expect(body).not.toContain('Your ISA, pension and other accounts');
	});

	it('no as_of ⇒ no stamp anywhere — never fabricated', () => {
		const body = html(
			[
				{ type: 'portfolioHeader', settings: { variant: 'canvas' } },
				{ type: 'holdings', settings: { variant: 'table' } }
			],
			trust.data
		);
		expect(body).not.toContain('as at');
	});
});

describe('AllocationDonut (SSR)', () => {
	it('renders the FINAL arcs server-side — the draw-in must not ship an empty donut', () => {
		const { body } = render(AllocationDonut, {
			props: {
				data: [
					{ label: 'Equities', value: 60, color: 'var(--brand)' },
					{ label: 'Bonds', value: 40, color: 'var(--muted)' }
				],
				total: 100_000,
				interactive: true
			}
		});
		// A full arc path is a real curve (an A command), not a degenerate point.
		expect(body).toMatch(/d="M[^"]*A/);
		expect(body).toContain('Equities');
	});

	it('the ring is presentational: no per-arc buttons, the legend is the interactive surface', () => {
		const { body } = render(AllocationDonut, {
			props: {
				data: [{ label: 'Equities', value: 100, color: 'var(--brand)' }],
				total: 1000
			}
		});
		expect(body).not.toContain('role="button"');
		expect(body).toContain('aria-label="Asset allocation"');
		expect(body).toContain('<button');
	});

	it('inert render disables the legend rows', () => {
		const { body } = render(AllocationDonut, {
			props: {
				data: [{ label: 'Equities', value: 100, color: 'var(--brand)' }],
				total: 1000,
				interactive: false
			}
		});
		expect(body).toContain('disabled');
	});
});

describe('portfolio return basis (SSR)', () => {
	it('does not print a flat return when no return basis exists', () => {
		for (const component of [PortfolioHeader, PortfolioHeaderCompact, PortfolioHeaderLedger]) {
			const body = render(component, {
				props: { ...BASE, gain: 0, gainPct: null }
			}).body;
			expect(body).toContain('—');
			expect(body).not.toContain('0.0%');
		}
	});
});
