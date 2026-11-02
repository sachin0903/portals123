/**
 * Relay an upstream file response to the browser through a same-origin proxy, BUFFERED and BOUNDED.
 *
 * The portals proxy private object-store bytes (documents, supporting files, chat attachments,
 * guides) because the store is compose-internal — a presigned URL would not resolve in the browser.
 * They buffer rather than pipe because a chunked response with no Content-Length makes Chromium's
 * embedded PDF viewer render blank intermittently (the "200 but blank preview" bug).
 *
 * Buffering without a ceiling was the audit finding: `res.arrayBuffer()` will hold whatever the
 * upstream sends, so a misbehaving upstream (or a store object that slipped past the API's 25 MiB
 * upload cap) pins that much memory per concurrent request. This reads the body a chunk at a time
 * and stops the moment the total passes `maxBytes`, refusing a declared Content-Length over the cap
 * before reading a byte.
 */

/** The API's own per-object ceiling (`storage.MAX_DOCUMENT_BYTES`), plus headroom for headers a
 *  container may add. Nothing the store holds should legitimately exceed it. */
export const MAX_RELAY_BYTES = 26 * 1024 * 1024;

export interface RelayOptions {
	/** Ceiling on the buffered body; defaults to {@link MAX_RELAY_BYTES}. */
	maxBytes?: number;
	/** `Content-Disposition` when the upstream sets none — `inline` for viewers, `attachment` for downloads. */
	fallbackDisposition?: 'inline' | 'attachment';
	/** `Content-Type` when the upstream sets none; defaults to `application/octet-stream`. */
	fallbackType?: string;
}

/** Thrown when the upstream body is over the cap; the caller decides the status to answer with. */
export class RelayTooLarge extends Error {
	constructor(public readonly maxBytes: number) {
		super(`Upstream body exceeds ${maxBytes} bytes`);
		this.name = 'RelayTooLarge';
	}
}

/** Read `res`'s body into one buffer, refusing to hold more than `maxBytes`. */
export async function readBounded(res: Response, maxBytes: number): Promise<Uint8Array> {
	const declared = Number(res.headers.get('content-length'));
	if (Number.isFinite(declared) && declared > maxBytes) throw new RelayTooLarge(maxBytes);
	if (!res.body) return new Uint8Array(0);
	const reader = res.body.getReader();
	const chunks: Uint8Array[] = [];
	let total = 0;
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		total += value.byteLength;
		if (total > maxBytes) {
			await reader.cancel().catch(() => {});
			throw new RelayTooLarge(maxBytes);
		}
		chunks.push(value);
	}
	const out = new Uint8Array(total);
	let offset = 0;
	for (const chunk of chunks) {
		out.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return out;
}

/**
 * The browser-facing response for an OK upstream: the bytes, the upstream's content type and
 * disposition (or the fallback), an explicit Content-Length (what the PDF viewer needs), and
 * `private, no-store`.
 */
export async function relayBuffered(res: Response, opts: RelayOptions = {}): Promise<Response> {
	const bytes = await readBounded(res, opts.maxBytes ?? MAX_RELAY_BYTES);
	return new Response(bytes as unknown as BodyInit, {
		headers: {
			'content-length': String(bytes.byteLength),
			'content-type':
				res.headers.get('content-type') ?? opts.fallbackType ?? 'application/octet-stream',
			'content-disposition':
				res.headers.get('content-disposition') ?? opts.fallbackDisposition ?? 'inline',
			'cache-control': 'private, no-store'
		}
	});
}
