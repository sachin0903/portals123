/**
 * One W3C trace per page render, held for the API calls that render makes.
 *
 * The browser never talks to the collector. The portal server mints the trace, and every
 * fetch to the API while that request is in flight carries the same `traceparent`.
 * Concurrent requests each have their own.
 */

type TraceStore = { traceparent: string };

type TraceStorage = {
	getStore(): TraceStore | undefined;
	run<T>(store: TraceStore, fn: () => T): T;
};

/**
 * Loaded on the first server request. A static `node:async_hooks` import is evaluated by
 * every client that imports this package (the UI barrel re-exports it), and that throws
 * in the browser before the page can hydrate.
 */
let storage: TraceStorage | null | undefined;
let loading: Promise<TraceStorage | null> | null = null;

function ensureStorage(): Promise<TraceStorage | null> {
	if (storage !== undefined) return Promise.resolve(storage);
	if (typeof window !== 'undefined') {
		storage = null;
		return Promise.resolve(null);
	}
	if (!loading) {
		loading = import('node:async_hooks').then(({ AsyncLocalStorage }) => {
			storage = new AsyncLocalStorage<TraceStore>();
			return storage;
		});
	}
	return loading;
}

export type PageTrace = {
	traceId: string;
	spanId: string;
	traceparent: string;
};

function randomHex(bytes: number): string {
	const buffer = new Uint8Array(bytes);
	crypto.getRandomValues(buffer);
	return [...buffer].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** A new unsampled trace. Flags stay `00` so the API's ratio can still apply. */
export function mintPageTrace(): PageTrace {
	const traceId = randomHex(16);
	const spanId = randomHex(8);
	return { traceId, spanId, traceparent: `00-${traceId}-${spanId}-00` };
}

export function currentTraceParent(): string | null {
	return storage?.getStore()?.traceparent ?? null;
}

export async function withTraceParent<T>(traceparent: string, run: () => T): Promise<Awaited<T>> {
	const current = await ensureStorage();
	if (!current) return await run();
	return await current.run({ traceparent }, run);
}
