import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import CameraCapture from './camera-capture.svelte';
import { DETECT_EVERY_MS, HOLD_MS } from './autocapture.js';
import type { FaceFinder } from './face-detector.js';

const heic = vi.hoisted(() => ({ heicTo: vi.fn() }));
vi.mock('heic-to/csp', () => heic);

const pdfjs = vi.hoisted(() => ({ getDocument: vi.fn(), destroy: vi.fn(async () => {}) }));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: pdfjs.getDocument }));

vi.useFakeTimers({ toFake: ['Date'] });

// The real face finder is MediaPipe's WebAssembly, which jsdom cannot run. No finder loads unless
// a test hands one over.
const { loadFaceFinder } = vi.hoisted(() => ({ loadFaceFinder: vi.fn() }));
vi.mock('./face-detector.js', () => ({ loadFaceFinder }));

type Mounted = { host: HTMLElement; component: ReturnType<typeof mount> };

function fakeStream(): { stream: MediaStream; stop: ReturnType<typeof vi.fn> } {
	const stop = vi.fn();
	const track = { kind: 'video', stop };
	const stream = {
		getTracks: () => [track],
		getVideoTracks: () => [track]
	} as unknown as MediaStream;
	return { stream, stop };
}

function stubMediaDevices(getUserMedia: (c: MediaStreamConstraints) => Promise<MediaStream>) {
	const spy = vi.fn(getUserMedia);
	Object.defineProperty(navigator, 'mediaDevices', {
		value: { getUserMedia: spy },
		configurable: true
	});
	return spy;
}

function render(props: Record<string, unknown>): Mounted {
	const host = document.createElement('div');
	document.body.appendChild(host);
	const component = mount(CameraCapture, {
		target: host,
		props: { label: 'Front of your passport', oncapture: () => {}, ...props } as never
	});
	flushSync();
	return { host, component };
}

function destroy({ host, component }: Mounted): void {
	unmount(component);
	host.remove();
}

function button(host: HTMLElement, text: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
	if (!found) throw new Error(`No "${text}" button in: ${host.textContent}`);
	return found;
}

async function settle(): Promise<void> {
	for (let i = 0; i < 20; i++) await Promise.resolve();
	flushSync();
}

/** Wait until ``check`` passes: for work behind a lazily loaded module (the HEIC decoder, pdf.js). */
async function until<T>(check: () => T): Promise<T> {
	return vi.waitFor(() => {
		flushSync();
		return check();
	});
}

const alertText = (host: HTMLElement) => host.querySelector('p[role="alert"]')?.textContent;

/** Hand the picker a file, as the browser does when one is chosen. */
function choose(host: HTMLElement, file: File): void {
	const input = host.querySelector('input[type="file"]') as HTMLInputElement;
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input.dispatchEvent(new Event('change', { bubbles: true }));
}

/** Drop a file on the component, as the browser does at the end of a drag. */
function drop(host: HTMLElement, file: File): Event {
	const event = new Event('drop', { bubbles: true, cancelable: true });
	Object.defineProperty(event, 'dataTransfer', { value: { types: ['Files'], files: [file] } });
	host.querySelector('[role="group"]')?.dispatchEvent(event);
	return event;
}

function pdf(bytes: number, name = 'Scan 2026.pdf'): File {
	return new File([new Uint8Array(bytes)], name, { type: 'application/pdf' });
}

/** A file that reports ``bytes`` without holding them. */
function sized(file: File, bytes: number): File {
	Object.defineProperty(file, 'size', { value: bytes, configurable: true });
	return file;
}

/** A chosen photo of a usual size: under the 20 MB a chosen file may be. */
function photo(name: string, type = 'image/jpeg'): File {
	return sized(new File(['jpeg'], name, { type }), 50_000);
}

/** A HEIC photo as an iPhone writes it: its type is often blank once it leaves the phone. */
function heicPhoto(name = 'IMG_0001.HEIC'): File {
	const head = new Uint8Array([0, 0, 0, 0x18, ...new TextEncoder().encode('ftypheic'), 0, 0, 0, 0]);
	return sized(new File([head], name, { type: '' }), 50_000);
}

/** The PDF pdf.js opens: ``pages`` pages of ``width`` x ``height`` points. */
function pdfPage(width: number, height: number, pages = 1) {
	const page = {
		getViewport: ({ scale }: { scale: number }) => ({
			width: width * scale,
			height: height * scale
		}),
		render: vi.fn(() => ({ promise: Promise.resolve() }))
	};
	pdfjs.getDocument.mockImplementation(() => ({
		promise: Promise.resolve({ numPages: pages, getPage: async () => page }),
		destroy: pdfjs.destroy
	}));
	return page;
}

/** A JPEG encoder whose output grows with the canvas and the quality: about `perPixel` bytes a
 *  pixel at quality 1. Records each encoding as `<width>x<height>@<quality>`. */
function sizedEncoder(perPixel: number): string[] {
	const encodings: string[] = [];
	vi.mocked(HTMLCanvasElement.prototype.toBlob).mockImplementation(function (
		this: HTMLCanvasElement,
		callback,
		_type,
		quality
	) {
		const q = quality ?? 1;
		encodings.push(`${this.width}x${this.height}@${q}`);
		const bytes = Math.round(this.width * this.height * q * perPixel);
		callback(new Blob([new Uint8Array(bytes)], { type: 'image/jpeg' }));
	});
	return encodings;
}

/** Animation frames that run only when told to, at the time given. */
function frames(): { run: (now: number) => Promise<void> } {
	let queue: FrameRequestCallback[] = [];
	vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
		queue.push(callback);
		return queue.length;
	});
	vi.stubGlobal('cancelAnimationFrame', () => {
		queue = [];
	});
	return {
		run: async (now) => {
			const due = queue;
			queue = [];
			for (const callback of due) callback(now);
			await settle();
		}
	};
}

/** A face finder that sees the faces ``seen`` returns, each a box in viewfinder fractions. */
function finder(seen: () => { x: number; y: number; width: number; height: number }[]): void {
	vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(4);
	loadFaceFinder.mockResolvedValue((() => seen()) satisfies FaceFinder);
}

/** A face in the middle of the selfie viewfinder, at arm's length. */
const CENTRED = { x: 0.3, y: 0.35, width: 0.4, height: 0.3 };
/** The same face, too far away. */
const FAR = { x: 0.4, y: 0.425, width: 0.2, height: 0.15 };

beforeEach(() => {
	Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
	loadFaceFinder.mockReset().mockResolvedValue(null);
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
	vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(1280);
	vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(720);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
		drawImage: vi.fn()
	} as unknown as CanvasRenderingContext2D);
	// Inside the size window, so a photo is encoded once.
	vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (callback) {
		callback(new Blob([new Uint8Array(50_000)], { type: 'image/jpeg' }));
	});
	URL.createObjectURL = vi.fn(() => 'blob:photo');
	URL.revokeObjectURL = vi.fn();
	pdfPage(595, 842);
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	heic.heicTo.mockReset();
	pdfjs.getDocument.mockReset();
	pdfjs.destroy.mockClear();
	vi.useFakeTimers({ toFake: ['Date'] });
	Reflect.deleteProperty(navigator, 'mediaDevices');
});

describe('CameraCapture', () => {
	it('opens the rear camera for a document and shows it in the viewfinder', async () => {
		const { stream } = fakeStream();
		const getUserMedia = stubMediaDevices(async () => stream);
		const view = render({ mode: 'document' });
		await settle();

		expect(getUserMedia).toHaveBeenCalledOnce();
		const video = getUserMedia.mock.calls[0][0].video as MediaTrackConstraints;
		expect(video.facingMode).toBe('environment');
		const element = view.host.querySelector('video') as HTMLVideoElement;
		expect(element.srcObject).toBe(stream);
		expect(element.classList.contains('-scale-x-100')).toBe(false);
		expect(view.host.querySelector('[data-slot="camera-guide"]')?.getAttribute('data-shape')).toBe(
			'card'
		);
		expect(view.host.querySelector('[role="group"]')?.getAttribute('aria-label')).toBe(
			'Front of your passport'
		);
		expect(button(view.host, 'Take photo').disabled).toBe(false);
		destroy(view);
	}, 20_000);

	it('opens the front camera for a face, unmirrored, and uses an oval guide', async () => {
		const { stream } = fakeStream();
		const getUserMedia = stubMediaDevices(async () => stream);
		const view = render({ mode: 'face', label: 'A selfie' });
		await settle();

		const video = getUserMedia.mock.calls[0][0].video as MediaTrackConstraints;
		expect(video.facingMode).toBe('user');
		// Not mirrored: the person sees the photo that is sent, facing the way their ID shows them.
		expect(view.host.querySelector('video')?.classList.contains('-scale-x-100')).toBe(false);
		// The browser's own buttons (picture in picture, Edge's Enhance) are kept off the feed.
		expect(view.host.querySelector('video')?.hasAttribute('disablepictureinpicture')).toBe(true);
		expect(view.host.querySelector('[data-slot="camera-shield"]')).not.toBeNull();
		expect(view.host.querySelector('[data-slot="camera-guide"]')?.getAttribute('data-shape')).toBe(
			'oval'
		);
		destroy(view);
	});

	it('takes a JPEG, stops the camera, and hands the file over on "Use photo"', async () => {
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', fileName: 'passport-front', oncapture });
		await settle();

		button(view.host, 'Take photo').click();
		await settle();

		expect(stop).toHaveBeenCalled();
		expect(view.host.querySelector('video')).toBeNull();
		expect(view.host.querySelector('img')?.getAttribute('alt')).toBe(
			'Front of your passport: your photo'
		);
		expect(oncapture).not.toHaveBeenCalled();

		button(view.host, 'Use photo').click();
		flushSync();

		expect(oncapture).toHaveBeenCalledOnce();
		const file = oncapture.mock.calls[0][0] as File;
		expect(file).toBeInstanceOf(File);
		expect(file.type).toBe('image/jpeg');
		expect(file.name).toBe('passport-front.jpg');
		destroy(view);
	});

	it('reopens the camera on Retake', async () => {
		const first = fakeStream();
		const second = fakeStream();
		const getUserMedia = stubMediaDevices(async () => first.stream);
		getUserMedia.mockResolvedValueOnce(first.stream).mockResolvedValueOnce(second.stream);
		const view = render({ mode: 'document' });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		button(view.host, 'Retake').click();
		await settle();

		expect(getUserMedia).toHaveBeenCalledTimes(2);
		expect((view.host.querySelector('video') as HTMLVideoElement).srcObject).toBe(second.stream);
		destroy(view);
	});

	it('saves a selfie in portrait, as the viewfinder shows it, from a landscape webcam', async () => {
		const { stream } = fakeStream();
		stubMediaDevices(async () => stream);
		const drawImage = vi.fn();
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage
		} as unknown as CanvasRenderingContext2D);
		const view = render({ mode: 'face', label: 'A selfie' });
		await settle();

		button(view.host, 'Take photo').click();
		await settle();

		// The webcam frame is 1280x720; the saved photo is its centred 3:4 part.
		expect(drawImage).toHaveBeenCalledWith(expect.anything(), 370, 0, 540, 720, 0, 0, 540, 720);
		destroy(view);
	});

	it('falls back to an upload when the camera is denied', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		expect(view.host.querySelector('video')).toBeNull();
		expect(view.host.textContent).toContain('Camera access is blocked');
		const input = view.host.querySelector('input[type="file"]') as HTMLInputElement;
		expect(input).not.toBeNull();
		expect(input.accept).toBe('image/*');
		expect(input.getAttribute('capture')).toBe('user');
		expect(button(view.host, 'Try the camera again')).toBeTruthy();

		const close = vi.fn();
		const decode = vi.fn(async () => ({ width: 4032, height: 3024, close }));
		vi.stubGlobal('createImageBitmap', decode);
		const drawImage = vi.fn();
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage
		} as unknown as CanvasRenderingContext2D);

		const chosen = photo('IMG_0001.HEIC', 'image/heic');
		Object.defineProperty(input, 'files', { value: [chosen], configurable: true });
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		button(view.host, 'Use photo').click();
		flushSync();

		// The upload is re-encoded, not passed on: a JPEG, cut down to the longest-side limit.
		expect(decode).toHaveBeenCalledWith(chosen);
		expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2560, 1920);
		expect(close).toHaveBeenCalledOnce();
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent).not.toBe(chosen);
		expect(sent.type).toBe('image/jpeg');
		expect(sent.name).toBe('selfie.jpg');
		destroy(view);
	});

	it('refuses an uploaded file the browser cannot read as an image', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => {
				throw new DOMException('Bad image', 'InvalidStateError');
			})
		);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', oncapture });
		await settle();

		choose(view.host, photo('notes.txt', 'text/plain'));
		await settle();

		expect(view.host.querySelector('p[role="alert"]')?.textContent).toBe(
			'That file could not be read. Choose a JPG, PNG or HEIC photo, or a PDF.'
		);
		expect(view.host.textContent).not.toContain('Use photo');
		expect(heic.heicTo).not.toHaveBeenCalled();
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('decodes a HEIC photo the browser cannot read, and sends it as a JPEG', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => {
				throw new DOMException('Unsupported', 'InvalidStateError');
			})
		);
		const close = vi.fn();
		heic.heicTo.mockResolvedValue({ width: 4032, height: 3024, close });
		const drawImage = vi.fn();
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage
		} as unknown as CanvasRenderingContext2D);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', fileName: 'id-front', oncapture });
		await settle();

		const chosen = heicPhoto();
		choose(view.host, chosen);
		await until(() => button(view.host, 'Use photo'));
		// An uploaded photo is chosen again, not retaken.
		expect(button(view.host, 'Change choice')).toBeTruthy();
		expect(view.host.textContent).not.toContain('Retake');
		button(view.host, 'Use photo').click();
		flushSync();

		expect(heic.heicTo).toHaveBeenCalledWith({ blob: chosen, type: 'bitmap' });
		expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2560, 1920);
		expect(close).toHaveBeenCalledOnce();
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.type).toBe('image/jpeg');
		expect(sent.name).toBe('id-front.jpg');
		destroy(view);
	});

	it('refuses a HEIC photo that cannot be decoded', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => {
				throw new DOMException('Unsupported', 'InvalidStateError');
			})
		);
		heic.heicTo.mockRejectedValue(new Error('corrupt'));
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		choose(view.host, heicPhoto());

		await until(() =>
			expect(alertText(view.host)).toBe(
				'That file could not be read as a photo. Choose a JPG, PNG or HEIC photo.'
			)
		);
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('refuses a file over 20 MB before it reads it', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		const decode = vi.fn();
		vi.stubGlobal('createImageBitmap', decode);
		const view = render({ mode: 'document' });
		await settle();

		choose(view.host, sized(new File(['jpeg'], 'big.jpg', { type: 'image/jpeg' }), 20_000_001));
		await settle();
		expect(view.host.querySelector('p[role="alert"]')?.textContent).toBe(
			'That file is too large. Choose one under 20 MB.'
		);

		choose(view.host, sized(pdf(1), 20_000_001));
		await settle();
		expect(view.host.querySelector('p[role="alert"]')?.textContent).toBe(
			'That file is too large. Choose one under 20 MB.'
		);
		expect(decode).not.toHaveBeenCalled();
		expect(pdfjs.getDocument).not.toHaveBeenCalled();
		destroy(view);
	});

	it('shows the formats and the size limit before a file is chosen', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const choice = render({ mode: 'document', startWith: 'choice' });
		await settle();
		expect(choice.host.textContent).toContain(
			'A photo (JPG, PNG or HEIC) or a PDF, up to 20 MB. You can also drag a file here.'
		);
		destroy(choice);

		const upload = render({ mode: 'face', label: 'A selfie', preferUpload: true });
		await settle();
		expect(upload.host.textContent).toContain(
			'Or drag it here. A photo (JPG, PNG or HEIC), up to 20 MB.'
		);
		destroy(upload);
	});

	it('takes a file dropped on the upload box', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => ({ width: 1600, height: 1200, close: () => {} }))
		);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		const event = drop(view.host, photo('me.jpg'));
		await settle();

		expect(event.defaultPrevented).toBe(true);
		button(view.host, 'Use photo').click();
		flushSync();
		expect((oncapture.mock.calls[0][0] as File).name).toBe('selfie.jpg');
		destroy(view);
	});

	it('takes a PDF dropped on the two choices', async () => {
		const getUserMedia = stubMediaDevices(async () => fakeStream().stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', startWith: 'choice', fileName: 'id-back', oncapture });
		await settle();

		drop(view.host, pdf(50_000));
		await until(() =>
			expect(view.host.querySelector('[data-slot="captured-file-card"]')).not.toBeNull()
		);

		expect(getUserMedia).not.toHaveBeenCalled();
		button(view.host, 'Use file').click();
		flushSync();
		expect((oncapture.mock.calls[0][0] as File).name).toBe('id-back.pdf');
		destroy(view);
	});

	it('ignores a file dropped on the live camera, and keeps the browser from opening it', async () => {
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const decode = vi.fn();
		vi.stubGlobal('createImageBitmap', decode);
		const view = render({ mode: 'document' });
		await settle();

		const event = drop(view.host, photo('licence.jpg'));
		await settle();

		expect(event.defaultPrevented).toBe(true);
		expect(decode).not.toHaveBeenCalled();
		expect(stop).not.toHaveBeenCalled();
		expect(view.host.querySelector('video')).not.toBeNull();
		destroy(view);
	});

	it('ignores a file dropped on a preview, and keeps the browser from opening it', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const view = render({ mode: 'document', startWith: 'choice', fileName: 'id-front' });
		await settle();
		choose(view.host, pdf(50_000));
		await until(() =>
			expect(view.host.querySelector('[data-slot="captured-file-card"]')).not.toBeNull()
		);

		const event = drop(view.host, pdf(60_000, 'other.pdf'));
		await settle();

		expect(event.defaultPrevented).toBe(true);
		expect(view.host.querySelector('[data-slot="captured-file-card"]')?.textContent?.trim()).toBe(
			'id-front.pdf, 49 KB'
		);
		destroy(view);
	});

	it('offers the device camera when a live view cannot be opened', async () => {
		const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
		const view = render({ mode: 'document' });
		await settle();

		expect(view.host.textContent).not.toContain('No camera available');
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		button(view.host, 'Take a photo').click();
		flushSync();

		expect(click).toHaveBeenCalledOnce();
		const native = view.host.querySelector('[data-slot="native-camera"]') as HTMLInputElement;
		expect(native.getAttribute('capture')).toBe('environment');
		expect(native.accept).toBe('image/*');
		destroy(view);
	});

	it('opens the front camera of the device for a face when a live view cannot', async () => {
		const view = render({ mode: 'face' });
		await settle();

		const native = view.host.querySelector('[data-slot="native-camera"]') as HTMLInputElement;
		expect(native.getAttribute('capture')).toBe('user');
		expect(view.host.textContent).not.toContain('No camera available');
		destroy(view);
	});

	it('lets the person choose an upload over a working camera', async () => {
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const view = render({ mode: 'document' });
		await settle();

		button(view.host, 'Upload a photo or PDF instead').click();
		flushSync();

		expect(stop).toHaveBeenCalled();
		expect(view.host.querySelector('input[type="file"]')).not.toBeNull();
		expect(button(view.host, 'Use the camera instead')).toBeTruthy();
		destroy(view);
	});

	it('offers two equal choices on a document step and starts no camera until one is chosen', async () => {
		const { stream } = fakeStream();
		const getUserMedia = stubMediaDevices(async () => stream);
		const view = render({ mode: 'document', startWith: 'choice' });
		await settle();

		expect(getUserMedia).not.toHaveBeenCalled();
		expect(view.host.querySelector('video')).toBeNull();
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(button(view.host, 'Upload a photo or PDF')).toBeTruthy();

		button(view.host, 'Take a photo').click();
		await settle();

		expect(getUserMedia).toHaveBeenCalledOnce();
		expect((view.host.querySelector('video') as HTMLVideoElement).srcObject).toBe(stream);
		destroy(view);
	});

	it('opens a picker for a photo or a PDF from the upload choice', async () => {
		const getUserMedia = stubMediaDevices(async () => fakeStream().stream);
		const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
		const view = render({ mode: 'document', startWith: 'choice' });
		await settle();

		button(view.host, 'Upload a photo or PDF').click();
		flushSync();

		expect(click).toHaveBeenCalledOnce();
		const input = view.host.querySelector('input[type="file"]') as HTMLInputElement;
		expect(input.accept).toBe('image/*,application/pdf');
		expect(input.getAttribute('capture')).toBeNull();
		expect(getUserMedia).not.toHaveBeenCalled();
		destroy(view);
	});

	it('passes a PDF scan on as it is and previews it as a file card', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const decode = vi.fn();
		vi.stubGlobal('createImageBitmap', decode);
		const oncapture = vi.fn();
		const view = render({
			mode: 'document',
			startWith: 'choice',
			fileName: 'id-front',
			oncapture
		});
		await settle();

		const chosen = pdf(50_000);
		choose(view.host, chosen);
		await until(() =>
			expect(view.host.querySelector('[data-slot="captured-file-card"]')).not.toBeNull()
		);

		expect(view.host.querySelector('img')).toBeNull();
		expect(view.host.querySelector('[data-slot="captured-file-card"]')?.textContent?.trim()).toBe(
			'id-front.pdf, 49 KB'
		);
		expect(URL.createObjectURL).not.toHaveBeenCalled();

		button(view.host, 'Use file').click();
		flushSync();

		expect(decode).not.toHaveBeenCalled();
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.name).toBe('id-front.pdf');
		expect(sent.type).toBe('application/pdf');
		expect(sent.size).toBe(50_000);
		destroy(view);
	});

	it('goes back to the two choices when another file is wanted', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const view = render({ mode: 'document', startWith: 'choice' });
		await settle();
		choose(view.host, pdf(50_000));
		await until(() =>
			expect(view.host.querySelector('[data-slot="captured-file-card"]')).not.toBeNull()
		);

		button(view.host, 'Change choice').click();
		flushSync();

		expect(view.host.querySelector('[data-slot="captured-file-card"]')).toBeNull();
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(button(view.host, 'Upload a photo or PDF')).toBeTruthy();
		destroy(view);
	});

	it('sends a PDF over 4 MB as its first page, a JPEG inside the size window', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const page = pdfPage(595, 842);
		const encodings = sizedEncoder(0.5);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', startWith: 'choice', fileName: 'id-front', oncapture });
		await settle();

		choose(view.host, pdf(4_000_001));
		await until(() => button(view.host, 'Use photo'));

		expect(page.render).toHaveBeenCalledOnce();
		expect(pdfjs.destroy).toHaveBeenCalledOnce();
		// A4 drawn at 2560 px on its longest side, then encoded once inside the window.
		expect(encodings).toEqual(['1809x2560@0.92']);
		expect(view.host.querySelector('[data-slot="captured-file-card"]')).toBeNull();
		expect(view.host.querySelector('img')?.getAttribute('alt')).toBe(
			'Front of your passport: your photo'
		);

		button(view.host, 'Use photo').click();
		flushSync();
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.name).toBe('id-front.jpg');
		expect(sent.type).toBe('image/jpeg');
		destroy(view);
	});

	it('enlarges a small chosen photo into the window, to be judged on the preview', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => ({ width: 300, height: 200, close: () => {} }))
		);
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage: vi.fn()
		} as unknown as CanvasRenderingContext2D);
		const encodings = sizedEncoder(0.3);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', startWith: 'choice', oncapture });
		await settle();

		choose(view.host, sized(photo('tiny.jpg'), 5_000));
		await settle();
		button(view.host, 'Use photo').click();
		flushSync();

		expect(encodings).toEqual(['300x200@0.92', '300x200@1', '450x300@1']);
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.size).toBeGreaterThanOrEqual(34 * 1024);
		destroy(view);
	});

	it('refuses an empty file before it is read', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const decode = vi.fn();
		vi.stubGlobal('createImageBitmap', decode);
		const view = render({ mode: 'document', startWith: 'choice' });
		await settle();

		choose(view.host, sized(photo('empty.jpg'), 0));
		await settle();
		expect(alertText(view.host)).toBe('That file is empty. Choose another.');
		expect(decode).not.toHaveBeenCalled();
		destroy(view);
	});

	it('refuses a selfie still too small once enlarged as far as it goes', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => ({ width: 2560, height: 1920, close: () => {} }))
		);
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage: vi.fn()
		} as unknown as CanvasRenderingContext2D);
		sizedEncoder(0.000001);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', oncapture });
		await settle();

		choose(view.host, sized(photo('me.jpg'), 1_000));
		await settle();
		expect(alertText(view.host)).toBe(
			'That photo is too small to use, even enlarged. Choose a larger one.'
		);
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('refuses a PDF of more than one page, naming the page wanted', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const page = pdfPage(595, 842, 3);
		const oncapture = vi.fn();
		const view = render({
			mode: 'document',
			startWith: 'choice',
			label: 'The front of your driving licence',
			oncapture
		});
		await settle();

		for (const bytes of [50_000, 4_000_001]) {
			choose(view.host, pdf(bytes));
			await until(() =>
				expect(alertText(view.host)).toBe(
					'Your PDF has 3 pages. Upload only the front of your driving licence.'
				)
			);
		}
		expect(page.render).not.toHaveBeenCalled();
		expect(view.host.querySelector('[data-slot="captured-file-card"]')).toBeNull();
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('refuses a PDF it cannot read', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		pdfjs.getDocument.mockImplementation(() => ({
			promise: Promise.reject(new Error('Invalid PDF structure')),
			destroy: pdfjs.destroy
		}));
		const oncapture = vi.fn();
		const view = render({ mode: 'document', startWith: 'choice', oncapture });
		await settle();

		choose(view.host, pdf(4_000_001));

		await until(() =>
			expect(alertText(view.host)).toBe('That PDF could not be read. Choose a different file.')
		);
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('keeps a face camera-first, with a photo-only upload that refuses a PDF', async () => {
		const { stream } = fakeStream();
		const getUserMedia = stubMediaDevices(async () => stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		expect(getUserMedia).toHaveBeenCalledOnce();
		button(view.host, 'Upload a photo instead').click();
		flushSync();
		const input = view.host.querySelector('input[type="file"]') as HTMLInputElement;
		expect(input.accept).toBe('image/*');

		choose(view.host, pdf(50_000));
		await settle();

		expect(view.host.querySelector('p[role="alert"]')?.textContent).toBe(
			'A PDF cannot be used here. Choose a JPG, PNG or HEIC photo.'
		);
		expect(view.host.querySelector('[data-slot="captured-file-card"]')).toBeNull();
		expect(oncapture).not.toHaveBeenCalled();
		destroy(view);
	});

	it('enlarges a low-resolution webcam selfie until it is inside the size window', async () => {
		const { stream } = fakeStream();
		stubMediaDevices(async () => stream);
		vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(640);
		vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(480);
		const drawImage = vi.fn();
		vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({
			drawImage
		} as unknown as CanvasRenderingContext2D);
		const encodings = sizedEncoder(0.12);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		button(view.host, 'Take photo').click();
		await settle();
		button(view.host, 'Use photo').click();
		flushSync();

		// 360x480 is too small at any quality; half as big again, it is inside the window.
		expect(encodings).toEqual(['360x480@0.92', '360x480@1', '540x720@1']);
		expect(drawImage).toHaveBeenLastCalledWith(expect.anything(), 0, 0, 540, 720);
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.size).toBe(46_656);
		expect(sent.size).toBeGreaterThanOrEqual(34 * 1024);
		expect(sent.name).toBe('selfie.jpg');
		destroy(view);
	});

	it('lowers the quality, then the size, of an upload too large for the window', async () => {
		stubMediaDevices(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		vi.stubGlobal(
			'createImageBitmap',
			vi.fn(async () => ({ width: 4032, height: 3024, close: () => {} }))
		);
		const encodings = sizedEncoder(1.5);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', oncapture });
		await settle();

		choose(view.host, photo('IMG_0001.jpg'));
		await settle();
		button(view.host, 'Use photo').click();
		flushSync();

		expect(encodings).toEqual(['2560x1920@0.92', '2560x1920@0.7', '1920x1440@0.7']);
		const sent = oncapture.mock.calls[0][0] as File;
		expect(sent.size).toBeLessThanOrEqual(4_000_000);
		expect(sent.type).toBe('image/jpeg');
		destroy(view);
	});

	it('takes a selfie by itself once the face is in the oval and still', async () => {
		const clock = frames();
		finder(() => [CENTRED]);
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();

		await clock.run(0);
		const hint = view.host.querySelector('[data-slot="camera-placement"]');
		expect(hint?.textContent?.trim()).toBe('Hold still');
		expect(hint?.getAttribute('role')).toBe('status');
		expect(view.host.querySelector('[data-slot="camera-guide"]')?.hasAttribute('data-placed')).toBe(
			true
		);

		for (let now = DETECT_EVERY_MS; now <= HOLD_MS; now += DETECT_EVERY_MS) await clock.run(now);

		// Taken, not sent: the person still looks at it and chooses Use photo.
		expect(stop).toHaveBeenCalled();
		expect(view.host.querySelector('video')).toBeNull();
		expect(view.host.querySelector('img')?.getAttribute('alt')).toBe('A selfie: your photo');
		expect(oncapture).not.toHaveBeenCalled();
		button(view.host, 'Use photo').click();
		flushSync();
		expect((oncapture.mock.calls[0][0] as File).name).toBe('selfie.jpg');
		destroy(view);
	});

	it('tells the person how to move until the face is placed', async () => {
		const clock = frames();
		let seen = [FAR];
		finder(() => seen);
		stubMediaDevices(async () => fakeStream().stream);
		const view = render({ mode: 'face', label: 'A selfie' });
		await settle();

		const hint = () =>
			view.host.querySelector('[data-slot="camera-placement"]')?.textContent?.trim();
		const placed = () =>
			view.host.querySelector('[data-slot="camera-guide"]')?.hasAttribute('data-placed');

		await clock.run(0);
		expect(hint()).toBe('Move closer');
		expect(placed()).toBe(false);

		seen = [];
		await clock.run(DETECT_EVERY_MS);
		expect(hint()).toBe('Put your face in the oval');

		seen = [CENTRED, FAR];
		await clock.run(2 * DETECT_EVERY_MS);
		expect(hint()).toBe('Only your face should be in the picture');

		seen = [CENTRED];
		await clock.run(3 * DETECT_EVERY_MS);
		expect(hint()).toBe('Hold still');
		expect(placed()).toBe(true);
		destroy(view);
	});

	it('keeps Take photo, with no hint, where the face finder does not load', async () => {
		const clock = frames();
		stubMediaDevices(async () => fakeStream().stream);
		const view = render({ mode: 'face', label: 'A selfie' });
		await settle();
		await clock.run(0);

		expect(loadFaceFinder).toHaveBeenCalledOnce();
		expect(view.host.querySelector('[data-slot="camera-placement"]')).toBeNull();
		button(view.host, 'Take photo').click();
		await settle();
		expect(view.host.querySelector('img')?.getAttribute('alt')).toBe('A selfie: your photo');
		destroy(view);
	});

	it('watches again after Retake', async () => {
		const clock = frames();
		finder(() => [CENTRED]);
		const first = fakeStream();
		const second = fakeStream();
		const getUserMedia = stubMediaDevices(async () => first.stream);
		getUserMedia.mockResolvedValueOnce(first.stream).mockResolvedValueOnce(second.stream);
		const view = render({ mode: 'face', label: 'A selfie' });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		button(view.host, 'Retake').click();
		await settle();
		for (let now = 0; now <= HOLD_MS; now += DETECT_EVERY_MS) await clock.run(now);

		expect(loadFaceFinder).toHaveBeenCalledTimes(2);
		expect(second.stop).toHaveBeenCalled();
		expect(view.host.querySelector('img')?.getAttribute('alt')).toBe('A selfie: your photo');
		destroy(view);
	});

	it('does not look for a face when it is a document, or while disabled', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const documentView = render({ mode: 'document' });
		await settle();
		const disabledView = render({ mode: 'face', label: 'A selfie', disabled: true });
		await settle();

		expect(loadFaceFinder).not.toHaveBeenCalled();
		destroy(documentView);
		destroy(disabledView);
	});

	it('warns about a photo the check doubts, and uses it only once the person confirms', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const check = vi.fn(async () => [
			'This looks like a driving licence, not a passport. Check you chose the right photo ID.'
		]);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', check, oncapture });
		await settle();

		button(view.host, 'Take photo').click();
		await settle();

		expect(check).toHaveBeenCalledOnce();
		expect((check.mock.calls[0] as unknown as [File])[0].type).toBe('image/jpeg');
		const warning = view.host.querySelector('[data-slot="camera-check"]');
		expect(warning?.getAttribute('role')).toBe('alert');
		expect(warning?.textContent).toContain('Check this photo');
		expect(warning?.textContent).toContain('This looks like a driving licence, not a passport.');

		button(view.host, 'Use photo').click();
		await settle();
		expect(oncapture).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('Use this photo anyway?');
		expect(document.querySelector('[data-slot="camera-check-confirm"]')?.textContent).toContain(
			'This looks like a driving licence, not a passport.'
		);

		// Go back closes the question and keeps the photo, with Retake beside it.
		button(document.body, 'Go back').click();
		await settle();
		expect(document.body.textContent).not.toContain('Use this photo anyway?');
		expect(oncapture).not.toHaveBeenCalled();
		expect(button(view.host, 'Retake')).toBeTruthy();

		button(view.host, 'Use photo').click();
		await settle();
		button(document.body, 'Use it anyway').click();
		await settle();
		expect(oncapture).toHaveBeenCalledOnce();
		destroy(view);
	});

	it('uses a photo the check had nothing to say about at once', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'document', check: vi.fn(async () => []), oncapture });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		button(view.host, 'Use photo').click();
		await settle();
		expect(document.body.textContent).not.toContain('Use this photo anyway?');
		expect(oncapture).toHaveBeenCalledOnce();
		destroy(view);
	});

	it('goes Back from the camera to the two choices, rather than offering a second upload', async () => {
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const view = render({ mode: 'document', startWith: 'choice' });
		await settle();
		button(view.host, 'Take a photo').click();
		await settle();

		expect(view.host.textContent).not.toContain('Upload a photo or PDF instead');
		button(view.host, 'Back').click();
		flushSync();

		expect(stop).toHaveBeenCalled();
		expect(view.host.querySelector('video')).toBeNull();
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(button(view.host, 'Upload a photo or PDF')).toBeTruthy();
		destroy(view);
	});

	it('says it is checking until the check answers, and shows nothing for a clean photo', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		let answer: (warnings: string[]) => void = () => {};
		const check = vi.fn(() => new Promise<string[]>((resolve) => (answer = resolve)));
		const view = render({ mode: 'document', check });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		expect(view.host.textContent).toContain('Checking the photo…');
		// Use photo waits for the check.
		expect(button(view.host, 'Use photo').disabled).toBe(true);

		answer([]);
		await settle();
		expect(view.host.textContent).not.toContain('Checking the photo…');
		expect(button(view.host, 'Use photo').disabled).toBe(false);
		expect(view.host.querySelector('[data-slot="camera-check"]')).toBeNull();
		destroy(view);
	});

	it('drops a check that answers after the photo was retaken', async () => {
		const getUserMedia = stubMediaDevices(async () => fakeStream().stream);
		getUserMedia.mockImplementation(async () => fakeStream().stream);
		const answers: ((warnings: string[]) => void)[] = [];
		const check = vi.fn(() => new Promise<string[]>((resolve) => answers.push(resolve)));
		const view = render({ mode: 'document', check });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();
		button(view.host, 'Retake').click();
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		// The new photo's check answers first; the old one's, arriving after, must not land.
		answers[1]?.([]);
		await settle();
		answers[0]?.(['A stale warning about the first photo.']);
		await settle();

		expect(check).toHaveBeenCalledTimes(2);
		expect(view.host.textContent).not.toContain('A stale warning');
		expect(view.host.textContent).not.toContain('Checking the photo…');
		destroy(view);
	});

	it('does not check a PDF, and shows nothing when the check fails', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const check = vi.fn(async (): Promise<string[]> => {
			throw new Error('reader failed');
		});
		const view = render({ mode: 'document', startWith: 'choice', check });
		await settle();

		choose(view.host, pdf(50_000));
		await until(() => button(view.host, 'Change choice'));
		expect(check).not.toHaveBeenCalled();

		button(view.host, 'Change choice').click();
		flushSync();
		button(view.host, 'Take a photo').click();
		await settle();
		button(view.host, 'Take photo').click();
		await settle();
		expect(check).toHaveBeenCalledOnce();
		expect(view.host.querySelector('[data-slot="camera-check"]')).toBeNull();
		expect(view.host.textContent).not.toContain('Checking the photo…');
		destroy(view);
	});

	it('stops saying it is checking when the check never answers', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const check = vi.fn(() => new Promise<string[]>(() => {}));
		const view = render({ mode: 'document', check });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();
		expect(view.host.textContent).toContain('Checking the photo…');

		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
		try {
			// The check's timer was set before the clock was faked, so a fresh check is started.
			button(view.host, 'Retake').click();
			await settle();
			button(view.host, 'Take photo').click();
			await settle();
			expect(view.host.textContent).toContain('Checking the photo…');
			vi.advanceTimersByTime(70_000);
			await settle();
			expect(view.host.textContent).not.toContain('Checking the photo…');
			expect(view.host.querySelector('[data-slot="camera-check"]')).toBeNull();
		} finally {
			vi.useFakeTimers({ toFake: ['Date'] });
		}
		destroy(view);
	});

	it('previews a camera selfie the way round it is sent, as the live view showed it', async () => {
		stubMediaDevices(async () => fakeStream().stream);
		const oncapture = vi.fn();
		const view = render({ mode: 'face', label: 'A selfie', oncapture });
		await settle();
		button(view.host, 'Take photo').click();
		await settle();

		expect(view.host.querySelector('img')?.classList.contains('-scale-x-100')).toBe(false);
		button(view.host, 'Use photo').click();
		flushSync();
		expect(oncapture).toHaveBeenCalledWith(expect.any(File));
		destroy(view);
	});

	it('stops the camera when it is destroyed', async () => {
		const { stream, stop } = fakeStream();
		stubMediaDevices(async () => stream);
		const view = render({ mode: 'document' });
		await settle();
		expect(stop).not.toHaveBeenCalled();

		destroy(view);

		expect(stop).toHaveBeenCalled();
	});

	it('releases a camera that opens after it was destroyed', async () => {
		const { stream, stop } = fakeStream();
		let open: (s: MediaStream) => void = () => {};
		stubMediaDevices(() => new Promise<MediaStream>((resolve) => (open = resolve)));
		const view = render({ mode: 'document' });
		expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce();
		destroy(view);

		open(stream);
		await settle();

		expect(stop).toHaveBeenCalled();
	});
});
