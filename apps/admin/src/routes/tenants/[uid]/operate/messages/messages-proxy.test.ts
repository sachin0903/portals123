import { describe, expect, it, vi } from 'vitest';
import { POST } from './api/[...path]/+server';

const baseLocals = {
	user: { id: 'ops', email: 'ops@example.test' },
	accessToken: 'token',
	permissions: ['tenant_portal:write'],
	role: null
};

function makeEvent(
	path: string,
	request: Request,
	fetch: typeof globalThis.fetch,
	permissions?: string[]
) {
	return {
		locals: { ...baseLocals, ...(permissions ? { permissions } : {}) },
		params: { uid: 'ten_target', path },
		request,
		fetch,
		url: new URL(`http://admin.test/messages/api/${path}`)
	};
}

describe('target messaging proxy', () => {
	it('forwards every multipart file and the target tenant', async () => {
		const incoming = new FormData();
		incoming.append('file', new File(['one'], 'one.txt'));
		incoming.append('file', new File(['two'], 'two.txt'));
		incoming.append('text', 'Both files');
		const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
			const url = new URL(String(input));
			expect(url.searchParams.get('tenant')).toBe('ten_target');
			expect((init?.body as FormData).getAll('file')).toHaveLength(2);
			return new Response(JSON.stringify({ id: 1 }), {
				headers: { 'content-type': 'application/json' }
			});
		});
		await (POST as unknown as (event: ReturnType<typeof makeEvent>) => Promise<Response>)(
			makeEvent(
				'conversations/conversation-1/attachments',
				new Request('http://admin.test', { method: 'POST', body: incoming }),
				fetch
			)
		);
		expect(fetch).toHaveBeenCalledOnce();
	});

	it('refuses writes for tenant-portal readers before calling the API', async () => {
		const fetch = vi.fn();
		const response = await (
			POST as unknown as (event: ReturnType<typeof makeEvent>) => Promise<Response>
		)(
			makeEvent(
				'conversations/conversation-1/messages',
				new Request('http://admin.test', {
					method: 'POST',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify({ text: 'No' })
				}),
				fetch,
				['tenant_portal:read']
			)
		);
		expect((response as Response).status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();
	});
});
