/**
 * The risk ramp: a 1..N rating as a colour between the theme's own success and destructive tokens.
 *
 * WHY A RAMP AND NOT THREE TONES. `statusToneClasses` gives success/warning/danger — three steps.
 * A risk rating of 4 and one of 5 would then look identical, which throws away most of what the
 * number is for. So the base colour is interpolated, and every stop of the interpolation is a
 * BRANDED token: a firm changing --success/--warning/--destructive re-themes the whole ramp, the
 * same guarantee tones.ts makes. No Tailwind palette colour appears here for exactly the reason
 * tones.ts states — it would defeat tenant branding in all three portals at once.
 *
 * PIECEWISE, THROUGH --warning. A single success→destructive mix would work (the hues are 162 and
 * 25, so the shorter arc already passes through amber) but its midpoint lands on yellow-green at
 * h≈94. Going via --warning instead puts mid-risk exactly on the caution token, which is the
 * meaning the design system already assigns to that colour, and uses all three of the tokens a
 * firm brands rather than two.
 *
 * `in oklch shorter hue` is deliberate, and is the one place hue interpolation is WANTED. theme.css
 * documents the opposite case at length: mixing a status token with the (slightly blue) background
 * dragged every tone toward blue, which is why the derived tints use `oklch(from …)` relative
 * syntax instead. Here the two endpoints are the ramp — travelling from one hue to the other is the
 * whole point. The subtle/strong/line trio is then derived from the result with that same relative
 * syntax (see risk-badge.svelte), so the ramp's hue is held exactly and it flips for dark mode off
 * the existing --tone-* knobs.
 */

/** A rating that could not be read as a point on the scale. */
export type RiskStep =
	{ kind: 'unrated'; label: string } | { kind: 'rated'; step: number; of: number; mix: string };

/** Clamp helper — a provider is free to send 0, 11 or 99 and none of them may break a table cell. */
const clamp = (value: number, low: number, high: number): number =>
	Math.min(high, Math.max(low, value));

/**
 * Read a rating onto the ramp.
 *
 * `rating` is whatever the record holds: `platform.models.risk_rating` is TEXT (Seccl publishes it
 * as a string, and a firm may type anything into it), so a non-numeric value is a real case rather
 * than a defensive flourish — it returns `unrated` and the badge renders it as plain text in the
 * neutral tone instead of pretending it is a 7.
 */
export function readRisk(rating: string | number | null | undefined, of = 10): RiskStep {
	const raw = typeof rating === 'number' ? String(rating) : (rating ?? '').trim();
	if (!raw) return { kind: 'unrated', label: '—' };

	const value = Number(raw);
	if (!Number.isFinite(value)) return { kind: 'unrated', label: raw };

	const top = Math.max(2, of);
	const step = clamp(value, 1, top);
	return { kind: 'rated', step, of: top, mix: riskMix(step, top) };
}

/**
 * The interpolated BASE colour for one step, as a CSS expression.
 *
 * The lower half travels success → warning, the upper half warning → destructive, so step 1 is the
 * success token exactly, the midpoint is the warning token exactly, and step `of` is the
 * destructive token exactly.
 */
export function riskMix(step: number, of = 10): string {
	const top = Math.max(2, of);
	const position = clamp(step, 1, top);
	const middle = (1 + top) / 2;

	if (position <= middle) {
		const t = ((position - 1) / (middle - 1)) * 100;
		return `color-mix(in oklch shorter hue, var(--success), var(--warning) ${round(t)}%)`;
	}
	const t = ((position - middle) / (top - middle)) * 100;
	return `color-mix(in oklch shorter hue, var(--warning), var(--destructive) ${round(t)}%)`;
}

/** Two decimals is plenty for a colour stop and keeps the inline style readable in devtools. */
const round = (value: number): number => Math.round(value * 100) / 100;
