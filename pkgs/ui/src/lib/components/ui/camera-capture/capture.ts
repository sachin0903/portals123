/**
 * The camera plumbing behind `CameraCapture`, kept out of the component so each rule is testable
 * without a browser.
 */

import type { PDFDocumentLoadingTask, PDFPageProxy } from 'pdfjs-dist';

/** What the camera is pointed at. A document is shot with the rear camera through a card-shaped
 *  guide; a face with the front camera through an oval. */
export type CameraCaptureMode = 'document' | 'face';

/** How `CameraCapture` opens. `camera` starts the camera at once. `choice` first offers "Take a
 *  photo" and an upload as two equal choices, and the camera starts only when it is chosen. */
export type CameraCaptureStart = 'choice' | 'camera';

/** Why the live camera is not on offer. `denied` is the person's (or the browser's) refusal and
 *  can be reversed in the browser settings; `unavailable` is a device with no usable camera. */
export type CameraProblem = 'denied' | 'unavailable';

/** The `getUserMedia` request for a mode. A plain `facingMode` string is a preference, not a
 *  requirement, so a laptop with one webcam still opens it for a document. */
export function cameraConstraints(mode: CameraCaptureMode): MediaStreamConstraints {
	return {
		audio: false,
		video: {
			facingMode: mode === 'face' ? 'user' : 'environment',
			width: { ideal: 1920 },
			height: { ideal: 1080 }
		}
	};
}

/** Sort a `getUserMedia` rejection into the two cases the fallback distinguishes. */
export function cameraProblem(error: unknown): CameraProblem {
	const name =
		typeof error === 'object' && error && 'name' in error ? String(error.name) : String(error);
	return name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError'
		? 'denied'
		: 'unavailable';
}

/** ``promise``'s value, or ``fallback`` once ``ms`` have passed without it settling. For work
 *  that may never answer, such as a WebAssembly engine that failed while it loaded. */
export function within<T, F>(promise: Promise<T>, ms: number, fallback: F): Promise<T | F> {
	return new Promise((resolve, reject) => {
		const timer = setTimeout(() => resolve(fallback), ms);
		promise.then(
			(value) => {
				clearTimeout(timer);
				resolve(value);
			},
			(error: unknown) => {
				clearTimeout(timer);
				reject(error);
			}
		);
	});
}

/** Release the camera. Stopping every track is what turns the device's camera light off. */
export function stopStream(stream: MediaStream | null | undefined): void {
	for (const track of stream?.getTracks() ?? []) track.stop();
}

/** The `capture` attribute for the upload picker. A face names the front camera, so a phone opens
 *  it. A document names none: its picker also offers files, a PDF scan among them, and a
 *  `capture` attribute would make a phone skip straight to the camera. */
export function captureHint(mode: CameraCaptureMode): 'user' | undefined {
	return mode === 'face' ? 'user' : undefined;
}

/** Whether this page can open a live camera. Outside a secure context (a phone opening the photo
 *  link over plain http) the browser hides `mediaDevices`, so `getUserMedia` cannot run. The
 *  device's own camera, opened through a file input, still can. */
export function liveCameraSupported(): boolean {
	return (
		typeof window !== 'undefined' &&
		window.isSecureContext &&
		typeof navigator.mediaDevices?.getUserMedia === 'function'
	);
}

/** The `capture` value that opens the device's own camera. A face names the front camera. */
export function nativeCapture(mode: CameraCaptureMode): 'user' | 'environment' {
	return mode === 'face' ? 'user' : 'environment';
}

export const PDF_TYPE = 'application/pdf';

/** The upload picker's `accept`. A document may be a photo or a PDF scan; a face is a photo (the
 *  check takes a live photo only as JPG or PNG). */
export function uploadAccept(mode: CameraCaptureMode): string {
	return mode === 'document' ? `image/*,${PDF_TYPE}` : 'image/*';
}

/** Whether a chosen file is a PDF, by its type or, where the browser gives none, its name. */
export function isPdfFile(file: Pick<File, 'type' | 'name'>): boolean {
	return file.type === PDF_TYPE || /\.pdf$/i.test(file.name);
}

/**
 * The size window every photo and scan is sent within. ComplyCube takes a document image only
 * "between 34 KB and 4 MB" for each side, and a live photo only when "the size must be between
 * 34 KB and 4 MB". Each bound is read on the safe side of either unit: 34 KiB and 4,000,000 bytes.
 */
export const MIN_BYTES = 34 * 1024;
export const MAX_BYTES = 4_000_000;

/** Whether a file of ``bytes`` is under, over or inside the size window. */
export function sizeProblem(bytes: number): 'small' | 'large' | null {
	if (bytes < MIN_BYTES) return 'small';
	if (bytes > MAX_BYTES) return 'large';
	return null;
}

/** The largest file the person may choose. A photo or PDF up to this size is brought inside the
 *  size window before it is sent — a big one shrunk, a small one enlarged — and the person judges
 *  it on the preview. A bigger one is refused before it is read. */
export const MAX_CHOSEN_BYTES = 20_000_000;

/** What the person is told about a file over ``MAX_CHOSEN_BYTES``. */
export const TOO_LARGE_ERROR = 'That file is too large. Choose one under 20 MB.';

/** What the person is told about a file with nothing in it. */
export const EMPTY_ERROR = 'That file is empty. Choose another.';

/** What the person is told about a photo still under the window once enlarged as far as it goes
 *  (``encodeWithinWindow``): the check will not take it. */
export function tooSmallError(mode: CameraCaptureMode): string {
	return mode === 'document'
		? 'That file is too small to use, even enlarged. Choose a larger photo or scan.'
		: 'That photo is too small to use, even enlarged. Choose a larger one.';
}

/** The formats and the size the person may choose, shown before they choose. */
export function uploadLimits(mode: CameraCaptureMode): string {
	return mode === 'document'
		? 'A photo (JPG, PNG or HEIC) or a PDF, up to 20 MB.'
		: 'A photo (JPG, PNG or HEIC), up to 20 MB.';
}

/** The ISO-BMFF brands of a HEIC or HEIF image: the ones the API's upload sniffing reads too. */
const HEIF_BRANDS = new Set(['heic', 'heix', 'hevc', 'hevx', 'mif1', 'msf1']);

/** Whether a file is a HEIC or HEIF image, by its first bytes: a browser often gives such a file
 *  no type, and a name can be wrong. */
export async function isHeicFile(file: Blob): Promise<boolean> {
	const head = new Uint8Array(await file.slice(0, 12).arrayBuffer());
	if (head.length < 12) return false;
	const text = String.fromCharCode(...head.subarray(4, 12));
	return text.startsWith('ftyp') && HEIF_BRANDS.has(text.slice(4));
}

/**
 * A chosen file as an image the canvas can draw. The browser decodes it where it can; a HEIC photo,
 * which only Safari decodes, is decoded by libheif (``heic-to``), loaded only when one is chosen.
 * Null when the file cannot be read as an image.
 */
export async function decodeImage(file: File): Promise<ImageBitmap | null> {
	if (typeof createImageBitmap !== 'function') return null;
	try {
		return await createImageBitmap(file);
	} catch {
		// Not an image this browser decodes: a HEIC photo is decoded below.
	}
	try {
		if (!(await isHeicFile(file))) return null;
		const { heicTo } = await import('heic-to/csp');
		return await heicTo({ blob: file, type: 'bitmap' });
	} catch {
		return null;
	}
}

/** What became of a chosen PDF: the file to send, or why it cannot be sent. */
export type PreparedPdf =
	{ file: File } | { error: 'unreadable' } | { error: 'pages'; pages: number };

/** What the person is told about a PDF of more than one page: a side of a document is one page,
 *  and ``label`` names it (e.g. "The front of your driving licence"). */
export function pdfPagesError(pages: number, label: string): string {
	const what = label ? label.charAt(0).toLowerCase() + label.slice(1) : 'the one page you need';
	return `Your PDF has ${pages} pages. Upload only ${what}.`;
}

/** The PDF's one page, drawn on a canvas at ``MAX_UPLOAD_SIDE`` on its longest side. Null when
 *  the browser gives no 2D context. */
async function drawPage(page: PDFPageProxy): Promise<HTMLCanvasElement | null> {
	const base = page.getViewport({ scale: 1 });
	const viewport = page.getViewport({ scale: MAX_UPLOAD_SIDE / Math.max(base.width, base.height) });
	const canvas = document.createElement('canvas');
	canvas.width = Math.round(viewport.width);
	canvas.height = Math.round(viewport.height);
	const context = canvas.getContext('2d');
	if (!context) return null;
	await page.render({ canvas, canvasContext: context, viewport }).promise;
	return canvas;
}

/**
 * A chosen PDF, ready to send. A side of a document is one page, so a PDF of more than one page is
 * refused: it is almost always the wrong file. A one-page PDF inside the size window is passed on
 * as it is (a scan is not re-encoded) under ``fileName``; one outside it is sent as that page, a
 * JPEG inside the window (``encodeWithinWindow``), because the browser cannot make a PDF smaller.
 * Unreadable when pdf.js cannot open it.
 */
export async function preparePdf(
	file: File,
	options: { fileName: string; quality: number }
): Promise<PreparedPdf> {
	let task: PDFDocumentLoadingTask | undefined;
	try {
		const [pdfjs, { default: workerUrl }] = await Promise.all([
			import('pdfjs-dist'),
			import('pdfjs-dist/build/pdf.worker.min.mjs?url')
		]);
		pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
		task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
		const pdf = await task.promise;
		if (pdf.numPages > 1) return { error: 'pages', pages: pdf.numPages };
		if (!sizeProblem(file.size)) {
			const sent = new File([file], `${options.fileName}.pdf`, {
				type: PDF_TYPE,
				lastModified: Date.now()
			});
			return { file: sent };
		}
		const canvas = await drawPage(await pdf.getPage(1));
		const jpeg = canvas ? await encodeWithinWindow(canvas, options) : null;
		return jpeg ? { file: jpeg } : { error: 'unreadable' };
	} catch {
		return { error: 'unreadable' };
	} finally {
		void task?.destroy();
	}
}

/** The longest side, in pixels, of an uploaded photo once it is re-encoded. Enough to read a
 *  document's small print, small enough that a phone's full-size photo still uploads. Also the
 *  furthest a photo that is too small is enlarged. */
export const MAX_UPLOAD_SIDE = 2560;

/** One way to encode a photo: its JPEG quality, and a scale on the size it was drawn at. */
export type Encoding = { quality: number; scale: number };

/** The JPEG quality a photo that is too small is re-encoded at before it is enlarged. */
export const SMALL_PHOTO_QUALITY = 1;
/** The JPEG quality a photo that is too large is re-encoded at before it is shrunk. */
export const LARGE_PHOTO_QUALITY = 0.7;
/** The scales, in order, a photo still too small at full quality is enlarged to. */
export const UPSCALES: readonly number[] = [1.5, 2, 3, 4];
/** Each shrink of a photo still too large at the lower quality. */
export const DOWNSCALE = 0.75;
/** At most this many encodings of one photo. The last one is kept even if it misses the window. */
export const MAX_ENCODINGS = 8;

/**
 * The encoding to try after ``last`` produced ``bytes``, or null when that one is inside the size
 * window or nothing is left to try.
 *
 * Under the window: full quality first, then enlarge (keeping the crop) up the ``UPSCALES``
 * ladder, never past ``MAX_UPLOAD_SIDE`` on the longest side. Over it: a lower quality first,
 * then shrink the longest side by ``DOWNSCALE`` each step. ``longestSide`` is the photo's longest
 * side at scale 1.
 */
export function nextEncoding(last: Encoding, bytes: number, longestSide: number): Encoding | null {
	const problem = sizeProblem(bytes);
	if (problem === 'small') {
		if (last.quality < SMALL_PHOTO_QUALITY) {
			return { quality: SMALL_PHOTO_QUALITY, scale: last.scale };
		}
		const step = UPSCALES.find((scale) => scale > last.scale);
		if (step === undefined || longestSide <= 0) return null;
		const scale = Math.min(step, MAX_UPLOAD_SIDE / longestSide);
		return scale > last.scale ? { quality: last.quality, scale } : null;
	}
	if (problem === 'large') {
		if (last.quality > LARGE_PHOTO_QUALITY) {
			return { quality: LARGE_PHOTO_QUALITY, scale: last.scale };
		}
		return { quality: last.quality, scale: last.scale * DOWNSCALE };
	}
	return null;
}

/** A canvas as a JPEG file. Null when the browser will not encode one. */
async function canvasToJpeg(
	canvas: HTMLCanvasElement,
	options: { fileName: string; quality: number }
): Promise<File | null> {
	const blob = await new Promise<Blob | null>((resolve) =>
		canvas.toBlob(resolve, 'image/jpeg', options.quality)
	);
	if (!blob) return null;
	return new File([blob], `${options.fileName}.jpg`, {
		type: 'image/jpeg',
		lastModified: Date.now()
	});
}

/** A copy of ``source`` at ``scale``. Null when the browser gives no 2D context. */
function scaledCanvas(source: HTMLCanvasElement, scale: number): HTMLCanvasElement | null {
	const canvas = document.createElement('canvas');
	canvas.width = Math.max(1, Math.round(source.width * scale));
	canvas.height = Math.max(1, Math.round(source.height * scale));
	const context = canvas.getContext('2d');
	if (!context) return null;
	context.imageSmoothingQuality = 'high';
	context.drawImage(source, 0, 0, canvas.width, canvas.height);
	return canvas;
}

/**
 * Encode the photo on ``source`` as a JPEG inside the size window (``MIN_BYTES``–``MAX_BYTES``),
 * re-encoding as ``nextEncoding`` decides, at most ``MAX_ENCODINGS`` times. Every encoding is
 * drawn from ``source``, so a camera frame is the same frame however often it is re-encoded. When
 * no encoding fits, the last one is returned: the largest for a photo too small, the smallest for
 * one too large. Null when the browser will not encode one.
 */
export async function encodeWithinWindow(
	source: HTMLCanvasElement,
	options: { fileName: string; quality: number }
): Promise<File | null> {
	const longestSide = Math.max(source.width, source.height);
	let encoding: Encoding = { quality: options.quality, scale: 1 };
	let file: File | null = null;
	for (let attempt = 0; attempt < MAX_ENCODINGS; attempt++) {
		const canvas = encoding.scale === 1 ? source : scaledCanvas(source, encoding.scale);
		if (!canvas) return file;
		const encoded = await canvasToJpeg(canvas, {
			fileName: options.fileName,
			quality: encoding.quality
		});
		if (!encoded) return file;
		file = encoded;
		const next = nextEncoding(encoding, encoded.size, longestSide);
		if (!next) return file;
		encoding = next;
	}
	return file;
}

/** The shape a photo is saved in, width over height: what the viewfinder shows for each mode. */
export function frameAspect(mode: CameraCaptureMode): number {
	return mode === 'face' ? 3 / 4 : 4 / 3;
}

/** The centred part of a ``width`` x ``height`` frame that has the ``aspect`` shape. */
export function centreCrop(
	width: number,
	height: number,
	aspect: number
): { x: number; y: number; width: number; height: number } {
	if (width / height > aspect) {
		const cropWidth = Math.round(height * aspect);
		return { x: Math.round((width - cropWidth) / 2), y: 0, width: cropWidth, height };
	}
	const cropHeight = Math.round(width / aspect);
	return { x: 0, y: Math.round((height - cropHeight) / 2), width, height: cropHeight };
}

/**
 * Grab the current frame of a playing video as a JPEG file, cropped to the viewfinder's shape
 * (``aspect``) so the photo is what the person framed: a selfie is portrait even from a landscape
 * webcam. The frame is not mirrored, and neither is the live view: the photo shows the face as it
 * is, as the person saw it. The JPEG is kept inside the size window
 * (``encodeWithinWindow``): a low-resolution webcam's frame is enlarged rather than sent too
 * small. Null when the video has no frame yet or the browser will not encode one.
 */
export async function captureFrame(
	video: HTMLVideoElement,
	options: { fileName: string; quality: number; aspect: number }
): Promise<File | null> {
	if (!video.videoWidth || !video.videoHeight) return null;
	const crop = centreCrop(video.videoWidth, video.videoHeight, options.aspect);
	const canvas = document.createElement('canvas');
	canvas.width = crop.width;
	canvas.height = crop.height;
	const context = canvas.getContext('2d');
	if (!context) return null;
	context.drawImage(video, crop.x, crop.y, crop.width, crop.height, 0, 0, crop.width, crop.height);
	return encodeWithinWindow(canvas, options);
}

/**
 * Re-encode an uploaded photo as a JPEG no larger than `maxSide` on its longest side, inside the
 * size window (``encodeWithinWindow``).
 *
 * Every photo upload goes through this, for three reasons: the check takes JPEG or PNG and a phone
 * may hand over HEIC (``decodeImage``); a phone's full-size photo can be too large to upload; and
 * drawing the image through a canvas drops its metadata, the GPS position a phone writes into a
 * photo among it. The orientation the phone recorded is applied as the image is decoded. Null when
 * the file cannot be read as an image.
 */
export async function normaliseUpload(
	file: File,
	options: { fileName: string; quality: number; maxSide?: number }
): Promise<File | null> {
	const bitmap = await decodeImage(file);
	if (!bitmap) return null;
	try {
		const longest = Math.max(bitmap.width, bitmap.height);
		if (!longest) return null;
		const scale = Math.min(1, (options.maxSide ?? MAX_UPLOAD_SIDE) / longest);
		const canvas = document.createElement('canvas');
		canvas.width = Math.round(bitmap.width * scale);
		canvas.height = Math.round(bitmap.height * scale);
		const context = canvas.getContext('2d');
		if (!context) return null;
		context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
		return encodeWithinWindow(canvas, options);
	} finally {
		bitmap.close();
	}
}
