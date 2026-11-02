import { describe, expect, it } from 'vitest';
import { MAX_RELAY_BYTES, RelayTooLarge, readBounded, relayBuffered } from './relay';

const chunked = (chunks: string[], headers: Record<string, string> = {}) =>
	new Response(
		new ReadableStream<Uint8Array>({
			start(controller) {
				for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
				controller.close();
			}
		}),
		{ headers }
	);

describe('readBounded', () => {
	it('reads a body under the cap whole', async () => {
		const bytes = await readBounded(chunked(['abc', 'def']), 100);
		expect(new TextDecoder().decode(bytes)).toBe('abcdef');
	});

	it('refuses a declared Content-Length over the cap before reading a byte', async () => {
		const res = chunked(['x'.repeat(50)], { 'content-length': '101' });
		await expect(readBounded(res, 100)).rejects.toBeInstanceOf(RelayTooLarge);
		expect(res.bodyUsed).toBe(false);
	});

	it('stops an undeclared body the moment it passes the cap', async () => {
		const res = chunked(['x'.repeat(60), 'y'.repeat(60), 'z'.repeat(60)]);
		await expect(readBounded(res, 100)).rejects.toBeInstanceOf(RelayTooLarge);
	});

	it('handles an empty body', async () => {
		expect((await readBounded(new Response(null), 10)).byteLength).toBe(0);
	});
});

describe('relayBuffered', () => {
	it('passes the upstream type and disposition through with a private no-store cache policy', async () => {
		const out = await relayBuffered(
			chunked(['%PDF'], {
				'content-type': 'application/pdf',
				'content-disposition': 'inline; filename="a.pdf"'
			})
		);
		expect(out.headers.get('content-type')).toBe('application/pdf');
		expect(out.headers.get('content-disposition')).toBe('inline; filename="a.pdf"');
		expect(out.headers.get('cache-control')).toBe('private, no-store');
		expect(out.headers.get('content-length')).toBe('4');
		expect(await out.text()).toBe('%PDF');
	});

	it('falls back to the caller\u2019s disposition and octet-stream', async () => {
		const out = await relayBuffered(chunked(['bytes']), { fallbackDisposition: 'attachment' });
		expect(out.headers.get('content-type')).toBe('application/octet-stream');
		expect(out.headers.get('content-disposition')).toBe('attachment');
	});

	it('defaults the cap to just over the API\u2019s 25 MiB object ceiling', () => {
		expect(MAX_RELAY_BYTES).toBe(26 * 1024 * 1024);
	});
});
