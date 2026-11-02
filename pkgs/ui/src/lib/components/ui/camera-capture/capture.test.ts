import { describe, expect, it, vi } from 'vitest';

const pages = vi.hoisted(() => ({ count: 1 }));
vi.mock('pdfjs-dist', () => ({
	GlobalWorkerOptions: {},
	getDocument: () => ({
		promise: Promise.resolve({ numPages: pages.count, getPage: async () => ({}) }),
		destroy: async () => {}
	})
}));
import {
	cameraConstraints,
	cameraProblem,
	captureHint,
	centreCrop,
	frameAspect,
	isHeicFile,
	isPdfFile,
	MAX_BYTES,
	MAX_CHOSEN_BYTES,
	MAX_ENCODINGS,
	MIN_BYTES,
	nextEncoding,
	pdfPagesError,
	preparePdf,
	sizeProblem,
	stopStream,
	uploadAccept,
	uploadLimits,
	within,
	type Encoding
} from './capture';

describe('cameraConstraints', () => {
	it('asks for the rear camera for a document and the front camera for a face', () => {
		expect((cameraConstraints('document').video as MediaTrackConstraints).facingMode).toBe(
			'environment'
		);
		expect((cameraConstraints('face').video as MediaTrackConstraints).facingMode).toBe('user');
		expect(cameraConstraints('face').audio).toBe(false);
	});

	it('opens the front camera from a face picker and names no camera for a document picker', () => {
		expect(captureHint('face')).toBe('user');
		expect(captureHint('document')).toBeUndefined();
	});
});

describe('within', () => {
	it('answers with the promise when it settles in time, and with the fallback when it does not', async () => {
		vi.useFakeTimers();
		try {
			await expect(within(Promise.resolve('read'), 1000, null)).resolves.toBe('read');
			const never = within(new Promise<string>(() => {}), 1000, null);
			vi.advanceTimersByTime(1000);
			await expect(never).resolves.toBeNull();
			await expect(within(Promise.reject(new Error('failed')), 1000, null)).rejects.toThrow(
				'failed'
			);
		} finally {
			vi.useRealTimers();
		}
	});
});

describe('uploadAccept', () => {
	it('takes a PDF scan for a document and only a photo for a face', () => {
		expect(uploadAccept('document')).toBe('image/*,application/pdf');
		expect(uploadAccept('face')).toBe('image/*');
	});
});

describe('isPdfFile', () => {
	it('reads a PDF by its type, or by its name when the browser gives no type', () => {
		expect(isPdfFile({ type: 'application/pdf', name: 'scan' })).toBe(true);
		expect(isPdfFile({ type: '', name: 'Scan.PDF' })).toBe(true);
		expect(isPdfFile({ type: 'image/jpeg', name: 'photo.jpg' })).toBe(false);
	});
});

describe('sizeProblem', () => {
	it('holds every file to the check’s 34 KB to 4 MB window', () => {
		expect(MIN_BYTES).toBe(34 * 1024);
		expect(MAX_BYTES).toBe(4_000_000);
		expect(sizeProblem(MIN_BYTES - 1)).toBe('small');
		expect(sizeProblem(MIN_BYTES)).toBeNull();
		expect(sizeProblem(MAX_BYTES)).toBeNull();
		expect(sizeProblem(MAX_BYTES + 1)).toBe('large');
	});
});

describe('preparePdf', () => {
	const chosen = () =>
		new File([new Uint8Array(50_000)], 'My scan.pdf', { type: 'application/pdf' });

	it('passes a one-page PDF inside the window on as it is, under the photo’s name', async () => {
		pages.count = 1;
		const prepared = await preparePdf(chosen(), { fileName: 'id-front', quality: 0.92 });
		const file = 'file' in prepared ? prepared.file : null;
		expect(file?.name).toBe('id-front.pdf');
		expect(file?.type).toBe('application/pdf');
		expect(file?.size).toBe(50_000);
	});

	it('refuses a PDF of more than one page', async () => {
		pages.count = 2;
		expect(await preparePdf(chosen(), { fileName: 'id-front', quality: 0.92 })).toEqual({
			error: 'pages',
			pages: 2
		});
	});
});

describe('pdfPagesError', () => {
	it('names the page wanted, from the box’s label', () => {
		expect(pdfPagesError(3, 'The photo page of your passport')).toBe(
			'Your PDF has 3 pages. Upload only the photo page of your passport.'
		);
		expect(pdfPagesError(2, '')).toBe('Your PDF has 2 pages. Upload only the one page you need.');
	});
});

describe('upload limits', () => {
	it('takes a chosen file up to 20 MB and says so before one is chosen', () => {
		expect(MAX_CHOSEN_BYTES).toBe(20_000_000);
		expect(uploadLimits('document')).toBe('A photo (JPG, PNG or HEIC) or a PDF, up to 20 MB.');
		expect(uploadLimits('face')).toBe('A photo (JPG, PNG or HEIC), up to 20 MB.');
	});
});

describe('isHeicFile', () => {
	const file = (brand: string) =>
		new Blob([new Uint8Array([0, 0, 0, 0x18]), `ftyp${brand}`, new Uint8Array(4)]);

	it('reads a HEIC or HEIF image by its first bytes, whatever its name or type', async () => {
		for (const brand of ['heic', 'heix', 'hevc', 'hevx', 'mif1', 'msf1']) {
			expect(await isHeicFile(file(brand))).toBe(true);
		}
	});

	it('reads any other file as not HEIC', async () => {
		expect(await isHeicFile(file('avif'))).toBe(false);
		expect(await isHeicFile(file('mp42'))).toBe(false);
		expect(await isHeicFile(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])]))).toBe(false);
		expect(await isHeicFile(new Blob([]))).toBe(false);
	});
});

describe('nextEncoding', () => {
	const at = (quality: number, scale = 1): Encoding => ({ quality, scale });
	const small = MIN_BYTES - 1;
	const large = MAX_BYTES + 1;

	it('keeps an encoding inside the window', () => {
		expect(nextEncoding(at(0.92), MIN_BYTES, 1000)).toBeNull();
		expect(nextEncoding(at(0.92), MAX_BYTES, 1000)).toBeNull();
	});

	it('raises the quality of a photo that is too small, then enlarges it', () => {
		expect(nextEncoding(at(0.92), small, 480)).toEqual(at(1));
		expect(nextEncoding(at(1), small, 480)).toEqual(at(1, 1.5));
		expect(nextEncoding(at(1, 1.5), small, 480)).toEqual(at(1, 2));
		expect(nextEncoding(at(1, 2), small, 480)).toEqual(at(1, 3));
		expect(nextEncoding(at(1, 3), small, 480)).toEqual(at(1, 4));
		expect(nextEncoding(at(1, 4), small, 480)).toBeNull();
	});

	it('never enlarges a photo past the longest-side limit', () => {
		expect(nextEncoding(at(1, 1.5), small, 1280)).toEqual(at(1, 2));
		expect(nextEncoding(at(1, 2), small, 1280)).toBeNull();
		expect(nextEncoding(at(1), small, 2560)).toBeNull();
	});

	it('lowers the quality of a photo that is too large, then shrinks it', () => {
		expect(nextEncoding(at(0.92), large, 2560)).toEqual(at(0.7));
		expect(nextEncoding(at(0.7), large, 2560)).toEqual(at(0.7, 0.75));
		expect(nextEncoding(at(0.7, 0.75), large, 2560)).toEqual(at(0.7, 0.5625));
	});

	/** Run the decisions against a size model, as `encodeWithinWindow` runs them on a canvas. */
	function settle(size: (encoding: Encoding) => number, longestSide: number): Encoding[] {
		const tried: Encoding[] = [];
		let encoding: Encoding | null = at(0.92);
		while (encoding && tried.length < MAX_ENCODINGS) {
			tried.push(encoding);
			encoding = nextEncoding(encoding, size(encoding), longestSide);
		}
		return tried;
	}

	it('brings a low-resolution webcam selfie up into the window', () => {
		// A 360x480 crop from a 640x480 webcam, about 0.12 bytes per pixel per unit of quality.
		const size = ({ quality, scale }: Encoding) => 360 * 480 * scale * scale * quality * 0.12;
		const tried = settle(size, 480);
		expect(tried).toEqual([at(0.92), at(1), at(1, 1.5)]);
		expect(sizeProblem(size(tried.at(-1) as Encoding))).toBeNull();
	});

	it('brings a large photo down into the window', () => {
		const size = ({ quality, scale }: Encoding) => 2560 * 1920 * scale * scale * quality * 1.5;
		const tried = settle(size, 2560);
		expect(tried).toEqual([at(0.92), at(0.7), at(0.7, 0.75)]);
		expect(sizeProblem(size(tried.at(-1) as Encoding))).toBeNull();
	});

	it('stops after a bounded number of encodings when nothing fits', () => {
		expect(settle(() => 1, 100).length).toBeLessThanOrEqual(MAX_ENCODINGS);
		expect(settle(() => MAX_BYTES * 100, 2560)).toHaveLength(MAX_ENCODINGS);
	});
});

describe('cameraProblem', () => {
	it('reads a refusal as denied', () => {
		for (const name of ['NotAllowedError', 'SecurityError', 'PermissionDeniedError']) {
			expect(cameraProblem({ name })).toBe('denied');
		}
	});

	it('reads anything else as no usable camera', () => {
		for (const name of ['NotFoundError', 'NotReadableError', 'OverconstrainedError']) {
			expect(cameraProblem({ name })).toBe('unavailable');
		}
		expect(cameraProblem(undefined)).toBe('unavailable');
	});
});

describe('stopStream', () => {
	it('stops every track and tolerates no stream', () => {
		const stop = vi.fn();
		stopStream({ getTracks: () => [{ stop }, { stop }] } as unknown as MediaStream);
		expect(stop).toHaveBeenCalledTimes(2);
		expect(() => stopStream(null)).not.toThrow();
	});
});

describe('centreCrop', () => {
	it('cuts a portrait selfie out of a landscape webcam frame', () => {
		expect(centreCrop(1280, 720, frameAspect('face'))).toEqual({
			x: 370,
			y: 0,
			width: 540,
			height: 720
		});
	});

	it('cuts a landscape document out of a portrait phone frame', () => {
		expect(centreCrop(1080, 1920, frameAspect('document'))).toEqual({
			x: 0,
			y: 555,
			width: 1080,
			height: 810
		});
	});

	it('keeps a frame that already has the shape', () => {
		expect(centreCrop(960, 1280, 3 / 4)).toEqual({ x: 0, y: 0, width: 960, height: 1280 });
	});
});
