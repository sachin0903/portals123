/**
 * The face finder behind selfie auto-capture: MediaPipe's face detector running BlazeFace (short
 * range, the model made for a front camera at arm's length) in the browser. Nothing leaves the
 * device: the frames are looked at in place, and the runtime and model are served from this
 * origin, not Google's CDN.
 *
 * MediaPipe is about 12 MB of WebAssembly, so it is loaded only when a selfie camera goes live,
 * once a page. A deployment whose `CSP_POLICY` sets `script-src` must allow `'wasm-unsafe-eval'`,
 * or the finder does not load and the person takes the photo with the button.
 */
// A package import (`@mediapipe/tasks-vision/…?url`) reaches the dev server as a Node module,
// query and all, and the page 500s. A relative file is served as an asset URL instead. The
// package is a dependency of this package, so pnpm places it in this package's node_modules.
import wasmLoaderUrl from '../../../../../node_modules/@mediapipe/tasks-vision/wasm/vision_wasm_internal.js?url';
import wasmBinaryUrl from '../../../../../node_modules/@mediapipe/tasks-vision/wasm/vision_wasm_internal.wasm?url';
import modelUrl from './blaze-face-short-range.tflite?url';
import type { FaceBox } from './autocapture.js';
import { centreCrop } from './capture.js';

/**
 * The faces in the viewfinder of a playing video: the centred part of its current frame in the
 * ``aspect`` shape (`centreCrop`), which is all the person sees and all the photo keeps. Each face
 * is a box in fractions of the viewfinder, 0–1 on each axis.
 */
export type FaceFinder = (video: HTMLVideoElement, aspect: number) => FaceBox[];

/** The lowest score a face is taken at. MediaPipe's default is 0.5; a little higher keeps a
 *  face-like pattern on a wall from counting as a second face. */
const MIN_CONFIDENCE = 0.6;

/** The longest side, in pixels, of the copy of the viewfinder the model is shown. The model
 *  itself looks at 128 x 128, so a larger copy is wasted work. */
const LOOK_SIDE = 320;

/** The faces in the whole of ``canvas``, as boxes in fractions of it. */
type Detect = (canvas: HTMLCanvasElement) => FaceBox[];

let loading: Promise<Detect | null> | undefined;

async function create(): Promise<Detect | null> {
	if (typeof WebAssembly === 'undefined') return null;
	const { FaceDetector, FilesetResolver } = await import('@mediapipe/tasks-vision');
	// Only the SIMD build is shipped. Every browser from 2023 on has SIMD; one without it gets the
	// button and is spared a 12 MB download it cannot run.
	if (!(await FilesetResolver.isSimdSupported())) return null;
	const detector = await FaceDetector.createFromOptions(
		{ wasmLoaderPath: wasmLoaderUrl, wasmBinaryPath: wasmBinaryUrl },
		{
			baseOptions: { modelAssetPath: modelUrl, delegate: 'CPU' },
			runningMode: 'VIDEO',
			minDetectionConfidence: MIN_CONFIDENCE
		}
	);
	let time = 0;
	return (canvas) => {
		// The detector refuses a time that does not grow, and one detector serves every caller.
		time = Math.max(performance.now(), time + 1);
		return detector.detectForVideo(canvas, time).detections.flatMap(({ boundingBox: box }) =>
			box
				? [
						{
							x: box.originX / canvas.width,
							y: box.originY / canvas.height,
							width: box.width / canvas.width,
							height: box.height / canvas.height
						}
					]
				: []
		);
	};
}

/** The detector, loaded on first use and kept for the page. Null where it cannot run here (no
 *  WebAssembly or SIMD) or did not load; a failed load is tried again on the next call. */
function loadDetector(): Promise<Detect | null> {
	loading ??= create().catch(() => {
		loading = undefined;
		return null;
	});
	return loading;
}

/** Start loading the detector, so it is ready by the time a photo is checked. */
export function prepareFaceFinder(): void {
	void loadDetector();
}

/** The face finder for a live video. Null where the detector cannot run here. */
export async function loadFaceFinder(): Promise<FaceFinder | null> {
	const detect = await loadDetector();
	const canvas = typeof document === 'undefined' ? null : document.createElement('canvas');
	const context = canvas?.getContext('2d');
	if (!detect || !canvas || !context) return null;
	return (video, aspect) => {
		// The model is shown the viewfinder only. Shown a whole landscape webcam frame, it shrinks
		// the face to fit and loses it at about arm's length.
		const crop = centreCrop(video.videoWidth, video.videoHeight, aspect);
		const scale = Math.min(1, LOOK_SIDE / Math.max(crop.width, crop.height));
		const width = Math.round(crop.width * scale);
		const height = Math.round(crop.height * scale);
		if (canvas.width !== width) canvas.width = width;
		if (canvas.height !== height) canvas.height = height;
		context.drawImage(video, crop.x, crop.y, crop.width, crop.height, 0, 0, width, height);
		return detect(canvas);
	};
}

/** The faces in a still image drawn on ``canvas``, as boxes in fractions of it. Null where the
 *  detector cannot run here. */
export async function findFacesIn(canvas: HTMLCanvasElement): Promise<FaceBox[] | null> {
	const detect = await loadDetector();
	return detect ? detect(canvas) : null;
}
