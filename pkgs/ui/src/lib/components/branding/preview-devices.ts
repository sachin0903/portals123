/**
 * The branding preview's simulated viewports.
 *
 * The preview renders the REAL portal shells, and the shells adapt to the VIEWPORT (Tailwind's
 * `md:` chrome swap, the mobile tab bar) — not to a container. A frame that is simply "as wide as
 * the editor column" (~450px) therefore always shows the mobile layout squeezed into a
 * desktop-shaped box. These specs are the internal viewport of the preview iframe: media queries
 * inside it evaluate against these numbers, so each device genuinely renders its own layout.
 *
 * Widths are chosen relative to the design system's `md` breakpoint (768px), where both shells
 * swap their chrome: desktop and iPad sit above it (top nav / sidebar), iPhone below it (bottom
 * tab bar). The test suite pins that relationship — moving a width across 768 changes WHICH
 * portal layout a device previews, which is a product decision, not a tweak.
 */
export interface PreviewDeviceSpec {
	/** Simulated viewport width in CSS px — what media queries inside the frame see. */
	width: number;
	/** Simulated viewport height in CSS px. */
	height: number;
	label: string;
}

export const PREVIEW_DEVICES = {
	desktop: { width: 1920, height: 1080, label: 'Desktop' },
	ipad: { width: 834, height: 1114, label: 'iPad' },
	iphone: { width: 390, height: 760, label: 'iPhone' }
} as const satisfies Record<string, PreviewDeviceSpec>;

export type PreviewDevice = keyof typeof PREVIEW_DEVICES;

/**
 * The zoom that fits a `deviceWidth`-wide frame into a `hostWidth`-wide column.
 *
 * Clamped to ≤ 1 — a 390px iPhone frame in a 700px column renders at natural size, never
 * upscaled (upscaling would blur text and lie about the device). Degenerate inputs are handled
 * explicitly rather than left to produce NaN/Infinity (which would poison the `height * scale`
 * layout maths downstream):
 * - a non-positive or non-finite `deviceWidth` (a divide-by-zero) ⇒ 1 (identity — show as-is);
 * - a non-finite `hostWidth` ⇒ 1;
 * - a negative `hostWidth` is treated as 0 (an unmeasured/hidden host ⇒ collapse, don't flash
 *   a full-size frame).
 */
export function fitScale(hostWidth: number, deviceWidth: number): number {
	if (!Number.isFinite(hostWidth) || !Number.isFinite(deviceWidth) || deviceWidth <= 0) return 1;
	return Math.min(1, Math.max(0, hostWidth) / deviceWidth);
}

/** The caption under the frame: `1920 × 1080 · 24%` (device dims + current zoom). */
export function frameCaption(width: number, height: number, scale: number): string {
	return `${width} × ${height} · ${Math.round(scale * 100)}%`;
}
