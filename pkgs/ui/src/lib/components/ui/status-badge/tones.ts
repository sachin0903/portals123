/**
 * The canonical status tone → class mapping.
 *
 * This is the ONE place in the product where a status colour is decided, which is why it lives in
 * a plain module rather than inside the component: it is unit-tested (`tones.test.ts`) and it is
 * the map every other surface should defer to instead of inventing its own.
 *
 * Every tone is a pure token lookup — three DERIVED tokens per status (see the tonal ramp in
 * `styles/theme.css`):
 *
 *     bg-{status}-subtle    the tinted surface
 *     text-{status}-strong  readable ink on that tint
 *     border-{status}-line  the hairline on that tint
 *
 * Each is `color-mix`'d from its BASE token against --background/--foreground, so one definition
 * serves light and dark, and a firm changing --success re-themes all three at once. Never reach for
 * a Tailwind palette class here (an emerald-700 ink, say): it would defeat tenant branding in all three
 * portals simultaneously.
 *
 * `primary` is the one tone still on the base-token + alpha idiom, because --primary has no derived
 * trio yet (it is a foreground/background PAIR token, so `text-primary` on `bg-primary/10` is
 * already high-contrast in both schemes — dark ink on a pale tint in light, pale teal on dark ink
 * in dark). If/when --primary-{subtle,strong,line} land it becomes a one-line change here.
 */
export const statusToneClasses = {
	success: 'border-success-line bg-success-subtle text-success-strong',
	warning: 'border-warning-line bg-warning-subtle text-warning-strong',
	info: 'border-info-line bg-info-subtle text-info-strong',
	danger: 'border-destructive-line bg-destructive-subtle text-destructive-strong',
	brand: 'border-brand-line bg-brand-subtle text-brand-strong',
	primary: 'border-primary/20 bg-primary/10 text-primary',
	neutral: 'border-border bg-muted text-muted-foreground'
};

/** The tones a StatusBadge (and anything deferring to this map) can carry. */
export type StatusToneName = keyof typeof statusToneClasses;
