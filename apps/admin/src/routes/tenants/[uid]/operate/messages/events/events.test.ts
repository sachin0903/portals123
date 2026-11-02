import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

type Handler = Parameters<typeof GET>[0];

describe('target-firm messaging events proxy', () => {
	it('scopes the ticket to the route tenant and streams events with buffering disabled', async () => {
		const stream = new ReadableStream<Uint8Array>();
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(new Response(JSON.stringify({ ticket: 'private-ticket' })))
			.mockResolvedValueOnce(new Response(stream));
		const request = new Request('http://admin.test/tenants/target%20firm/operate/messages/events');

		const response = await GET({
			locals: {
				user: { id: 'ops' },
				accessToken: 'access-token',
				permissions: ['tenant_portal:read']
			},
			params: { uid: 'target firm' },
			request,
			fetch
		} as unknown as Handler);

		expect(fetch).toHaveBeenNthCalledWith(
			1,
			'http://127.0.0.1:8000/messaging/ticket?tenant=target+firm',
			expect.objectContaining({
				method: 'POST',
				headers: { Authorization: 'Bearer access-token' },
				signal: request.signal
			})
		);
		expect(fetch).toHaveBeenNthCalledWith(
			2,
			'http://127.0.0.1:8000/messaging/events',
			expect.objectContaining({
				headers: {
					Accept: 'text/event-stream',
					'x-realtime-ticket': 'private-ticket'
				},
				signal: request.signal
			})
		);
		expect(response.body).toBe(stream);
		expect(response.headers.get('content-type')).toBe('text/event-stream');
		expect(response.headers.get('cache-control')).toBe('no-store, no-transform');
		expect(response.headers.get('x-accel-buffering')).toBe('no');
	});

	it('requires an authenticated local session', async () => {
		const fetch = vi.fn();
		const response = await GET({
			locals: { user: null, accessToken: null, permissions: [] },
			params: { uid: 'ten_target' },
			request: new Request('http://admin.test/tenants/ten_target/operate/messages/events'),
			fetch
		} as unknown as Handler);

		expect(response.status).toBe(401);
		expect(fetch).not.toHaveBeenCalled();
	});
});
