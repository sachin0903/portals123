import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	EMAIL_COPY_BOX,
	emailCopyFor,
	fitEmailBox,
	isSvg,
	sizedSvg,
	svgEmailCopy,
	svgSize
} from './email-copy';

// jsdom has no canvas and does not decode images, so the drawing itself is stubbed: what these pin
// is everything around it — which uploads get a copy, the size it is drawn at, and that a failure
// is "no copy" rather than a failed upload. The drawing was checked in real Chrome and Edge against
// the dev tenant's Illustrator SVG (viewBox only): 640x115, no network request, no script run.

// jsdom has no `HTMLImageElement.decode`; give it one to stub.
if (!('decode' in HTMLImageElement.prototype)) {
	Object.defineProperty(HTMLImageElement.prototype, 'decode', {
		value: () => Promise.resolve(),
		configurable: true,
		writable: true
	});
}

const svgFile = (markup: string, name = 'logo.svg', type = 'image/svg+xml') =>
	new File([markup], name, { type });

const ILLUSTRATOR = `<?xml version="1.0" encoding="UTF-8"?>
<svg id="Layer_2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1740.4 312"><rect width="9" height="9"/></svg>`;

function stubDrawing(png: Blob | null = new Blob(['png'], { type: 'image/png' })) {
	const drawn: { width: number; height: number }[] = [];
	vi.spyOn(HTMLImageElement.prototype, 'decode').mockResolvedValue(undefined);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (
		this: HTMLCanvasElement
	) {
		const { width, height } = this;
		return {
			drawImage: () => drawn.push({ width, height })
		} as unknown as CanvasRenderingContext2D;
	} as never);
	vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback, type) => {
		expect(type).toBe('image/png');
		callback(png);
	});
	URL.createObjectURL ??= () => 'blob:stub';
	URL.revokeObjectURL ??= () => undefined;
	return drawn;
}

afterEach(() => vi.restoreAllMocks());

describe('which uploads get a copy from the browser', () => {
	it('only an SVG logo or icon — the API converts every other type itself', async () => {
		stubDrawing();
		expect(await emailCopyFor('logo', svgFile(ILLUSTRATOR))).not.toBeNull();
		expect(await emailCopyFor('icon', svgFile(ILLUSTRATOR))).not.toBeNull();
		for (const kind of ['logo-dark', 'favicon', 'card-art']) {
			expect(await emailCopyFor(kind, svgFile(ILLUSTRATOR))).toBeNull();
		}
		const png = new File(['x'], 'logo.png', { type: 'image/png' });
		expect(await emailCopyFor('logo', png)).toBeNull();
	});

	it('knows an SVG by its type or, when the browser gives none, its name', () => {
		expect(isSvg(svgFile(ILLUSTRATOR))).toBe(true);
		expect(isSvg(svgFile(ILLUSTRATOR, 'Logo.SVG', ''))).toBe(true);
		expect(isSvg(new File(['x'], 'logo.webp', { type: 'image/webp' }))).toBe(false);
	});
});

describe('the size it is drawn at', () => {
	const root = (attrs: string) =>
		new DOMParser().parseFromString(
			`<svg xmlns="http://www.w3.org/2000/svg" ${attrs}/>`,
			'image/svg+xml'
		).documentElement;

	it('reads an Illustrator export, which carries only a viewBox', () => {
		expect(svgSize(root('viewBox="0 0 1740.4 312"'))).toEqual({ width: 1740.4, height: 312 });
	});

	it('prefers plain width and height, and ignores percentages', () => {
		expect(svgSize(root('width="200" height="50px" viewBox="0 0 1 1"'))).toEqual({
			width: 200,
			height: 50
		});
		expect(svgSize(root('width="100%" height="100%" viewBox="0 0 400 100"'))).toEqual({
			width: 400,
			height: 100
		});
		expect(svgSize(root('width="200" viewBox="0 0 400 100"'))).toEqual({ width: 200, height: 50 });
	});

	it('has no size when nothing says one', () => {
		expect(svgSize(root(''))).toBeNull();
		expect(svgSize(root('viewBox="0 0 0 10"'))).toBeNull();
	});

	it('fits the email box, aspect kept — and draws a small SVG up to it, having no pixels to lose', () => {
		expect(fitEmailBox({ width: 1740.4, height: 312 })).toEqual({ width: 640, height: 115 });
		expect(fitEmailBox({ width: 40, height: 40 })).toEqual({
			width: EMAIL_COPY_BOX.height,
			height: EMAIL_COPY_BOX.height
		});
	});

	it('states the drawn size on the markup, and a viewBox when the SVG had none to scale by', () => {
		const sized = sizedSvg('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="50"/>');
		expect(sized).toMatchObject({ width: 640, height: 160 });
		const out = new DOMParser().parseFromString(sized!.markup, 'image/svg+xml').documentElement;
		expect(out.getAttribute('viewBox')).toBe('0 0 200 50');
		expect(out.getAttribute('width')).toBe('640');
		expect(out.getAttribute('height')).toBe('160');
	});

	it('is nothing for a file that is not an SVG', () => {
		expect(sizedSvg('<html><body>hi</body></html>')).toBeNull();
		expect(sizedSvg('not markup at all <')).toBeNull();
	});
});

describe('drawing', () => {
	it('draws the PNG at the fitted size', async () => {
		const drawn = stubDrawing();
		const png = await svgEmailCopy(svgFile(ILLUSTRATOR));
		expect(png?.type).toBe('image/png');
		expect(drawn).toEqual([{ width: 640, height: 115 }]);
	});

	it('is null, never an error, when the browser cannot draw it', async () => {
		stubDrawing();
		vi.spyOn(HTMLImageElement.prototype, 'decode').mockRejectedValue(new Error('EncodingError'));
		expect(await svgEmailCopy(svgFile(ILLUSTRATOR))).toBeNull();
	});

	it('is null when the canvas will not encode (a tainted canvas throws)', async () => {
		stubDrawing();
		vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(() => {
			throw new DOMException('Tainted canvases may not be exported.', 'SecurityError');
		});
		expect(await svgEmailCopy(svgFile(ILLUSTRATOR))).toBeNull();
	});
});
