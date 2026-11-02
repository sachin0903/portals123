/**
 * The text reader behind the identity document check: Tesseract (tesseract.js) reading English
 * print, in a web worker in the browser. Like the face finder, nothing leaves the device, and the
 * worker, the WebAssembly core and the language data are all served from this origin, never the
 * CDN tesseract.js reaches for by default.
 *
 * About 7 MB in all (a 3.9 MB core and 3 MB of language data), loaded once a page, from the first
 * call to `prepareTextReader` or `readText`. A deployment whose `CSP_POLICY` sets `script-src`
 * must allow `'wasm-unsafe-eval'`, as for the face finder, or nothing is read and nothing is
 * warned about.
 */
import workerUrl from 'tesseract.js/dist/worker.min.js?url';
// The single-file build (the WebAssembly is inside the script): a separate .wasm would be looked
// for beside the script under its original name, which the build's hashed asset names break.
import coreUrl from 'tesseract.js-core/tesseract-core-simd-lstm.wasm.js?url';
import languageUrl from '@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz?url';
import type { Worker as TextWorker } from 'tesseract.js';
import { within } from './capture.js';

/** The longest the reader may take to load (about 7 MB) before the check goes on without it. */
const LOAD_TIMEOUT_MS = 45_000;
/** The longest one photo may take to read. */
const READ_TIMEOUT_MS = 20_000;

let loading: Promise<TextWorker | null> | undefined;

/** ``url`` as an absolute URL: the worker resolves a relative one against its own script. */
function absolute(url: string): string {
	return new URL(url, globalThis.location?.href).href;
}

/**
 * The `langPath` that makes tesseract.js fetch the bundled language file. It fetches
 * `<langPath>/eng.traineddata.gz` and nothing else, and the build names the file by its hash.
 * Ending the path in a query (`…/eng.traineddata.<hash>.gz?/eng.traineddata.gz`) keeps that
 * suffix out of the file's path, and a static file server ignores a query.
 *
 * Not the documented `{ code, data }` language object: tesseract.js 7 hands its `data` (the
 * bytes) to Tesseract where the language's name belongs, and every start fails with
 * "initialization failed".
 */
function languagePath(): string {
	return `${absolute(languageUrl)}?`;
}

async function create(): Promise<TextWorker | null> {
	if (typeof WebAssembly === 'undefined' || typeof Worker === 'undefined') return null;
	const { createWorker, OEM } = await import('tesseract.js');
	return createWorker('eng', OEM.LSTM_ONLY, {
		workerPath: absolute(workerUrl),
		corePath: absolute(coreUrl),
		langPath: languagePath(),
		// A same-origin worker needs no blob: URL, which a strict CSP would have to allow.
		workerBlobURL: false,
		// The browser's HTTP cache keeps the hashed file. IndexedDB would be a second copy.
		cacheMethod: 'none',
		logger: () => {},
		// Without a handler, a failure inside the worker is thrown where nothing can catch it, and
		// the job it belonged to never settles. The timeouts below are what end the wait.
		errorHandler: () => {}
	});
}

/** Start loading the reader, so it is ready by the time a photo is checked. */
export function prepareTextReader(): Promise<TextWorker | null> {
	loading ??= within(create(), LOAD_TIMEOUT_MS, null)
		.catch(() => null)
		.then((reader) => {
			// A load that failed or ran out of time is tried again on the next call.
			if (!reader) loading = undefined;
			return reader;
		});
	return loading;
}

/** The text printed in the image on ``canvas``, or null where it cannot be read here. */
export async function readText(canvas: HTMLCanvasElement): Promise<string | null> {
	const reader = await prepareTextReader();
	if (!reader) return null;
	try {
		const result = await within(reader.recognize(canvas), READ_TIMEOUT_MS, null);
		return result?.data.text ?? null;
	} catch {
		return null;
	}
}
