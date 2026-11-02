/**
 * A person's avatar tone (`PersonAvatar`).
 *
 * The discs are told apart by LIGHTNESS within the firm's brand hue, not by hue: four steps
 * from a pale wash to a deep slab, each pinned so its ink reads (the two pale steps carry a
 * dark ink of the brand hue, the two deep ones white). An eight-hue wheel told faces apart a
 * little better and put magenta and lime in every navy-and-gold firm's adviser stack; a
 * handful of faces with names beside them is told apart well enough by four tones.
 *
 * Each step is TWO background declarations on purpose: a plain-oklch fallback (off the stock
 * brand hue) for engines without relative colour syntax, then the brand-relative one.
 */
const BRAND_HUE = 202; // the stock --brand hue, used by the fallback only
const STEPS = [
	{ l: 0.9, c: 0.04, ink: 'dark' },
	{ l: 0.78, c: 0.08, ink: 'dark' },
	{ l: 0.55, c: 0.1, ink: 'light' },
	{ l: 0.38, c: 0.08, ink: 'light' }
] as const;
const DARK_INK = 'color: oklch(from var(--brand) 0.3 calc(min(c, 0.08)) h);';
const LIGHT_INK = 'color: var(--hero-foreground);';

export function personTone(name: string): string {
	let h = 0;
	for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
	const step = STEPS[h % STEPS.length] ?? STEPS[2];
	return (
		`background-color: oklch(${step.l} ${step.c} ${BRAND_HUE});` +
		`background-color: oklch(from var(--brand) ${step.l} ${step.c} h);` +
		(step.ink === 'dark' ? DARK_INK : LIGHT_INK)
	);
}
