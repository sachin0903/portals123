import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CHECK_SIDE, checkDocumentPhoto, readsText } from './document-check';

// The face finder and the text reader are WebAssembly, which jsdom cannot run.
const { findFacesIn, readText } = vi.hoisted(() => ({ findFacesIn: vi.fn(), readText: vi.fn() }));
vi.mock('./face-detector.js', () => ({ findFacesIn }));
vi.mock('./document-text.js', () => ({ readText }));

const LICENCE_TEXT = 'DRIVING LICENCE UK\n2. MR SARAH MEREDYTH\n5. MORGA753116SM9IJ 35';
const FACE = { x: 0.1, y: 0.3, width: 0.2, height: 0.3 };
const photo = new File(['jpeg'], 'id-front.jpg', { type: 'image/jpeg' });

beforeEach(() => {
	findFacesIn.mockReset().mockResolvedValue([]);
	readText.mockReset().mockResolvedValue(LICENCE_TEXT);
	vi.stubGlobal(
		'createImageBitmap',
		vi.fn(async () => ({ width: 4000, height: 3000, close: () => {} }))
	);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
		drawImage: vi.fn()
	} as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	Reflect.deleteProperty(navigator, 'userAgentData');
});

/** Report ``agent`` as the browser's user agent, and give no `userAgentData`. */
function userAgent(agent: string): void {
	vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(agent);
	Object.defineProperty(navigator, 'userAgentData', { value: undefined, configurable: true });
}

const IPHONE =
	'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const ANDROID =
	'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36';
const DESKTOP =
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';

describe('readsText', () => {
	it('reads text on a computer, not on a phone', () => {
		userAgent(DESKTOP);
		expect(readsText()).toBe(true);
		userAgent(IPHONE);
		expect(readsText()).toBe(false);
		userAgent(ANDROID);
		expect(readsText()).toBe(false);
	});

	it('takes the browser’s own answer where it gives one', () => {
		userAgent(DESKTOP);
		Object.defineProperty(navigator, 'userAgentData', {
			value: { mobile: true },
			configurable: true
		});
		expect(readsText()).toBe(false);
	});
});

describe('checkDocumentPhoto on a phone', () => {
	it('looks for the face only, and warns from that alone', async () => {
		userAgent(IPHONE);
		const warnings = await checkDocumentPhoto(photo, { kind: 'passport', side: 'front' });

		expect(readText).not.toHaveBeenCalled();
		expect(findFacesIn).toHaveBeenCalled();
		expect(warnings).toEqual([
			'We could not find a photo of a face. Check this is the photo page of your passport, and that all of it is in the photo.'
		]);
	});
});

describe('checkDocumentPhoto', () => {
	it('reads the photo at CHECK_SIDE and warns from what it found', async () => {
		const warnings = await checkDocumentPhoto(photo, { kind: 'passport', side: 'front' });

		const page = readText.mock.calls[0]?.[0] as HTMLCanvasElement;
		expect([page.width, page.height]).toEqual([CHECK_SIDE, 1200]);
		expect(warnings).toEqual([
			'We could not find a photo of a face. Check this is the photo page of your passport, and that all of it is in the photo.',
			'This looks like a driving licence, not a passport. Check you chose the right photo ID.'
		]);
	});

	it('counts a face found only when the photo side is looked at on its own', async () => {
		findFacesIn.mockResolvedValueOnce([]).mockResolvedValueOnce([FACE]);

		const warnings = await checkDocumentPhoto(photo, { kind: 'driving_licence', side: 'front' });

		expect(warnings).toEqual([]);
		const part = findFacesIn.mock.calls[1]?.[0] as HTMLCanvasElement;
		expect([part.width, part.height]).toEqual([880, 1200]);
	});

	it('warns about a face on the back', async () => {
		findFacesIn.mockResolvedValue([FACE]);
		readText.mockResolvedValue('4a. 01.04.2013 categories AM B1 C1E D1 valid from until codes');

		expect(await checkDocumentPhoto(photo, { kind: 'driving_licence', side: 'back' })).toEqual([
			'This side has a photo of a face, so it may be the front. Turn your driving licence over and take the back.'
		]);
	});

	it('warns about nothing when the photo cannot be read as an image', async () => {
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => {
				throw new DOMException('Bad image', 'InvalidStateError');
			})
		);

		expect(await checkDocumentPhoto(photo, { kind: 'passport', side: 'front' })).toEqual([]);
		expect(readText).not.toHaveBeenCalled();
	});

	it('warns about nothing when neither check can run here', async () => {
		findFacesIn.mockResolvedValue(null);
		readText.mockResolvedValue(null);

		expect(await checkDocumentPhoto(photo, { kind: 'passport', side: 'front' })).toEqual([]);
	});
});
