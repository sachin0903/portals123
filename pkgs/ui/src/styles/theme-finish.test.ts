import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { brandingHead } from '@graphene/auth';

/**
 * The finish-axis contract between theme.css (token defaults + the `[data-slot='card']`
 * consumption rules) and pkgs/auth branding.ts (the ledger FINISH_DECLS that re-point them).
 *
 * Two invariants:
 *  1. DEFAULTS ARE TODAY'S LOOK. The root declarations must equal, value for value, the classes
 *     they replaced on card.svelte/card-title.svelte — an un-customised tenant must render
 *     byte-identically to before the axis existed.
 *  2. NO ORPHANED TOKENS. Every custom property the ledger finish emits must be one theme.css
 *     declares (or documents as deliberately undeclared), otherwise a rename on either side
 *     silently strands the other and the finish stops carrying that surface.
 */

const css = readFileSync(new URL('./theme.css', import.meta.url), 'utf-8');

describe('finish-axis token defaults reproduce the pre-axis look', () => {
	it.each([
		// The card surface — must equal the old `bg-card border border-border/70` (Tailwind mixes
		// utility colour opacity in oklab, hence that interpolation space).
		['--surface-card', 'var(--card)'],
		['--surface-card-border', '1px solid color-mix(in oklab, var(--border) 70%, transparent)'],
		// The divider restates the border by default, so the carded look has no separate bottom edge.
		['--surface-card-divider', 'var(--surface-card-border)'],
		// Depth per elevation step — the same tokens Card's classes used to name directly.
		['--surface-card-shadow', 'var(--elev-1)'],
		['--surface-card-shadow-floating', 'var(--elev-3)'],
		// Card.Title register — the old `text-lg tracking-tight`.
		['--card-title-case', 'none'],
		['--card-title-tracking', '-0.025em'],
		['--card-title-size', '1.125rem'],
		// The glass-panel outer drop shadow, extracted verbatim from the utility body.
		['--glass-panel-shadow', '0 4px 18px -10px oklch(0.3 0.05 264 / 0.22)']
	])('%s defaults to %s', (token, value) => {
		expect(css).toContain(`${token}: ${value};`);
	});

	it('--money-display is deliberately NOT declared — unset means inherit the cell face', () => {
		// A root default would overwrite inheritance: mono reference columns must keep mono
		// figures and a display-face hero its numerals. Money relies on the failed var()
		// substitution behaving as `unset` (see money.svelte).
		expect(css).not.toMatch(/--money-display:/);
	});

	it('glass-panel consumes the shadow token instead of the old literal', () => {
		expect(css).toContain('var(--glass-panel-shadow),');
	});
});

describe('the components-layer consumption rules exist', () => {
	it('the card surface rule reads all three surface tokens', () => {
		const rule = css.match(/\[data-slot='card'\] \{[^}]*\}/)?.[0] ?? '';
		expect(rule).toContain('background-color: var(--surface-card);');
		expect(rule).toContain('border: var(--surface-card-border);');
		expect(rule).toContain('border-bottom: var(--surface-card-divider);');
	});

	it('the title rule reads the register knobs, and the sm card scales the size token', () => {
		const rule = css.match(/\[data-slot='card-title'\] \{[^}]*\}/)?.[0] ?? '';
		expect(rule).toContain('font-size: var(--card-title-size);');
		expect(rule).toContain('letter-spacing: var(--card-title-tracking);');
		expect(rule).toContain('text-transform: var(--card-title-case);');
		// 18 → 16px at the default 1.125rem: exactly the old group-data-[size=sm] text-base.
		expect(css).toContain('font-size: calc(var(--card-title-size) * 8 / 9);');
	});
});

describe('the studio-preview re-derivation hook', () => {
	/** brandingPreviewStyle overrides the glass knobs INLINE on a preview frame, but the derived
	 *  fills substitute their var()s at the DECLARING element (:root) — without a local
	 *  re-derivation the studio can never show the ledger finish's open panels (or the Material
	 *  fill slider) working. The hook's formulas must mirror the canonical :root/.dark ones. */
	it('re-derives every knob-driven glass var, with formulas identical to the canonical ones', () => {
		// `\s+` between the two selectors, NOT a literal `\n`. On a Windows checkout theme.css is
		// CRLF, so `\n` alone never matched, `darkHook` was the empty string, and this failed with
		// "--glass-bg missing from the dark hook" — a line-ending flake that reads as a real theme
		// defect, and was dismissed as pre-existing for exactly that reason. It passes or fails on the
		// CONTENT now, whichever way git checked the file out.
		const hook = css.match(/\[style\*='--glass'\] \{[^}]*\}/)?.[0] ?? '';
		const darkHook =
			css.match(/\.dark\[style\*='--glass'\],\s+\.dark \[style\*='--glass'\] \{[^}]*\}/)?.[0] ?? '';
		for (const derived of [
			'--glass-bg',
			'--glass-panel-bg',
			'--glass-highlight',
			'--glass-sheen'
		]) {
			for (const [name, block] of [
				['light hook', hook],
				['dark hook', darkHook]
			] as const) {
				const formula = block.match(new RegExp(`${derived}: ([^;]+);`))?.[1];
				expect(formula, `${derived} missing from the ${name}`).toBeTruthy();
				// The exact same declaration must exist somewhere OUTSIDE the hooks (the :root or
				// .dark canonical block) — i.e. at least twice in the file — so the two cannot drift.
				const occurrences = css.split(`${derived}: ${formula};`).length - 1;
				expect(
					occurrences,
					`${derived} hook formula must mirror a canonical block`
				).toBeGreaterThanOrEqual(2);
			}
		}
	});
});

describe('no orphaned ledger tokens (branding.ts ↔ theme.css drift pin)', () => {
	it('every custom property the ledger finish emits is declared here or documented unset', () => {
		const head = brandingHead({ finish: 'ledger' });
		const emitted = [...head.matchAll(/(--[a-z-]+):/g)].map((m) => m[1]);
		expect(emitted.length).toBeGreaterThan(10); // the ledger table really was emitted
		// --money-display is the one deliberate gap (unset ⇒ inherit); --radius predates the axis.
		const documentedUnset = new Set(['--money-display']);
		for (const token of emitted) {
			if (documentedUnset.has(token)) continue;
			expect(css, `${token} is emitted by the ledger finish but never declared`).toMatch(
				new RegExp(`${token}:`)
			);
		}
	});
});
