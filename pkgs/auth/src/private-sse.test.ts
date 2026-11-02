import { describe, expect, it, vi } from 'vitest';
import { privateSseResponse } from './private-sse';

const stream = () =>
	new ReadableStream<Uint8Array>({
		start(controller) {
			controller.enqueue(new TextEncoder().encode('event: notification\ndata: {}\n\n'));
			controller.close();
		}
	});

describe('privateSseResponse', () => {
	it('mints a ticket with bearer auth and opens the SSE stream without exposing it in a URL', async () => {
		const signal = new AbortController().signal;
		const body = stream();
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ ticket: 'single-use-secret' }), {
					headers: { 'content-type': 'application/json' }
				})
			)
			.mockResolvedValueOnce(new Response(body));

		const response = await privateSseResponse({
			fetch,
			apiBaseUrl: 'http://api.test/',
			accessToken: 'access-token',
			ticketPath: '/messaging/ticket?tenant=ten_target',
			eventsPath: '/messaging/events',
			signal
		});

		expect(fetch).toHaveBeenNthCalledWith(1, 'http://api.test/messaging/ticket?tenant=ten_target', {
			method: 'POST',
			headers: { Authorization: 'Bearer access-token' },
			signal
		});
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://api.test/messaging/events', {
			headers: {
				Accept: 'text/event-stream',
				'x-realtime-ticket': 'single-use-secret'
			},
			signal
		});
		expect(String(fetch.mock.calls[1][0])).not.toContain('single-use-secret');
		expect(response.body).toBe(body);
		expect(response.headers.get('content-type')).toBe('text/event-stream');
		expect(response.headers.get('cache-control')).toBe('no-store, no-transform');
		expect(response.headers.get('x-accel-buffering')).toBe('no');
	});

	it.each([
		['ticket', 403],
		['events', 503]
	])('preserves a useful %s error without labelling it as SSE', async (stage, status) => {
		const error = new Response(JSON.stringify({ detail: 'upstream failed' }), {
			status,
			headers: { 'content-type': 'application/json' }
		});
		const fetch =
			stage === 'ticket'
				? vi.fn().mockResolvedValue(error)
				: vi
						.fn()
						.mockResolvedValueOnce(new Response(JSON.stringify({ ticket: 'ticket' })))
						.mockResolvedValueOnce(error);

		const response = await privateSseResponse({
			fetch,
			apiBaseUrl: 'http://api.test',
			accessToken: 'token',
			ticketPath: '/notifications/ticket',
			eventsPath: '/notifications/events',
			signal: new AbortController().signal
		});

		expect(response.status).toBe(status);
		expect(response.headers.get('content-type')).toBe('application/json');
		expect(response.headers.get('content-type')).not.toBe('text/event-stream');
		expect(await response.json()).toEqual({ detail: 'upstream failed' });
	});

	it('passes the same abort signal to both upstream requests', async () => {
		const controller = new AbortController();
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(new Response(JSON.stringify({ ticket: 'ticket' })))
			.mockImplementationOnce(
				(_input, init) =>
					new Promise((_resolve, reject) => {
						init?.signal?.addEventListener('abort', () =>
							reject(new DOMException('Aborted', 'AbortError'))
						);
					})
			);

		const pending = privateSseResponse({
			fetch,
			apiBaseUrl: 'http://api.test',
			accessToken: 'token',
			ticketPath: '/notifications/ticket',
			eventsPath: '/notifications/events',
			signal: controller.signal
		});
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
		controller.abort();

		await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
		expect(fetch.mock.calls[0][1]?.signal).toBe(controller.signal);
		expect(fetch.mock.calls[1][1]?.signal).toBe(controller.signal);
	});
});
