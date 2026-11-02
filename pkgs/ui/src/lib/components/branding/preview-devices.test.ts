import { describe, expect, it } from 'vitest';
import { PREVIEW_DEVICES, fitScale, frameCaption } from './preview-devices.js';

describe('PREVIEW_DEVICES', () => {
	// The design system swaps both shells' chrome at Tailwind `md` (768px). Which side of that
	// line a device sits on decides WHICH portal layout it previews — desktop/iPad must show the
	// top-nav/sidebar chrome, iPhone the bottom tab bar. This was the original defect: a ~450px
	// frame meant the "desktop" preview rendered the mobile layout.
	const MD = 768;

	it('keeps desktop and iPad above the md breakpoint, iPhone below it', () => {
		expect(PREVIEW_DEVICES.desktop.width).toBeGreaterThanOrEqual(MD);
		expect(PREVIEW_DEVICES.ipad.width).toBeGreaterThanOrEqual(MD);
		expect(PREVIEW_DEVICES.iphone.width).toBeLessThan(MD);
	});

	it('declares positive, finite dimensions and a label for every device', () => {
		for (const spec of Object.values(PREVIEW_DEVICES)) {
			expect(spec.width).toBeGreaterThan(0);
			expect(spec.height).toBeGreaterThan(0);
			expect(Number.isFinite(spec.width)).toBe(true);
			expect(Number.isFinite(spec.height)).toBe(true);
			expect(spec.label.length).toBeGreaterThan(0);
		}
	});
});

describe('fitScale', () => {
	it('scales a wide device down to fit the host column', () => {
		expect(fitScale(960, 1920)).toBe(0.5);
		expect(fitScale(448, 1920)).toBeCloseTo(0.2333, 3);
	});

	it('never upscales — a narrow device in a wide column renders at natural size', () => {
		expect(fitScale(700, 390)).toBe(1);
		expect(fitScale(1920, 1920)).toBe(1);
	});

	it('collapses (0) for an unmeasured or hidden host rather than flashing full size', () => {
		expect(fitScale(0, 1920)).toBe(0);
		expect(fitScale(-10, 1920)).toBe(0);
	});

	it('guards divide-by-zero and non-finite inputs with the identity scale', () => {
		expect(fitScale(500, 0)).toBe(1);
		expect(fitScale(500, -1)).toBe(1);
		expect(fitScale(500, NaN)).toBe(1);
		expect(fitScale(NaN, 1920)).toBe(1);
		expect(fitScale(Infinity, 1920)).toBe(1);
		expect(fitScale(500, Infinity)).toBe(1);
	});

	it('always returns a finite value in [0, 1]', () => {
		for (const [host, device] of [
			[0, 0],
			[1e9, 1],
			[1, 1e9],
			[-5, -5],
			[NaN, NaN]
		]) {
			const s = fitScale(host, device);
			expect(Number.isFinite(s)).toBe(true);
			expect(s).toBeGreaterThanOrEqual(0);
			expect(s).toBeLessThanOrEqual(1);
		}
	});
});

describe('frameCaption', () => {
	it('renders device dims and the rounded zoom percentage', () => {
		expect(frameCaption(1920, 1080, 0.2333)).toBe('1920 × 1080 · 23%');
		expect(frameCaption(390, 760, 1)).toBe('390 × 760 · 100%');
	});
});
