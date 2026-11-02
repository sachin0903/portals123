import { describe, expect, it, vi } from 'vitest';
import {
	committedPagePath,
	handlePageViewRequest,
	recordPageView,
	shouldRecordPageView,
	type PageViewEvent
} from './page-view';

function event(over: Partial<PageViewEvent> = {}): PageViewEvent {
	return {
		request: { method: 'GET', headers: { get: () => null } },
		url: new URL('http://portal.test/team/abc?tab=1'),
		isSubRequest: false,
		locals: { accessToken: 'token' },
		...over
	};
}

function response(over: { status?: number; type?: string | null } = {}) {
	return {
		status: over.status ?? 200,
		headers: {
			get: (name: string) => (name === 'content-type' ? (over.type ?? 'text/html') : null)
		}
	};
}

describe('shouldRecordPageView', () => {
	it('accepts a successful HTML document and ignores a data request', () => {
		expect(shouldRecordPageView(event(), response())).toBe(true);
		expect(shouldRecordPageView(event(), response({ type: 'application/json' }))).toBe(false);
	});

	it('rejects subrequests and unsigned requests', () => {
		expect(shouldRecordPageView(event({ isSubRequest: true }), response())).toBe(false);
		expect(shouldRecordPageView(event({ locals: { accessToken: null } }), response())).toBe(false);
		expect(
			shouldRecordPageView(
				event({ request: { method: 'POST', headers: { get: () => null } } }),
				response()
			)
		).toBe(false);
		expect(shouldRecordPageView(event(), response({ status: 404 }))).toBe(false);
		expect(shouldRecordPageView(event(), response({ type: 'text/css' }))).toBe(false);
	});
});

describe('recordPageView', () => {
	it('posts the pathname only, and does not wait on the insert', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		recordPageView(event(), response(), { apiUrl: 'http://api.test/', fetch });
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
		const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/me/page-view');
		expect(JSON.parse(String(init.body))).toEqual({ path: '/team/abc' });
		expect(String(init.body)).not.toContain('tab');
	});

	it('does not post a view it would not count', () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		recordPageView(event({ isSubRequest: true }), response(), {
			apiUrl: 'http://api.test',
			fetch
		});
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('committedPagePath', () => {
	it('keeps a committed navigation and skips the document load', () => {
		expect(
			committedPagePath(
				{
					type: 'link',
					from: { url: { pathname: '/' } },
					to: { url: { pathname: '/team/abc' } }
				},
				200
			)
		).toBe('/team/abc');
		expect(
			committedPagePath(
				{
					type: 'link',
					from: { url: { pathname: '/' } },
					to: { url: { pathname: '/missing' } }
				},
				404
			)
		).toBe(null);
		expect(committedPagePath({ type: 'enter', to: { url: { pathname: '/team/abc' } } }, 200)).toBe(
			null
		);
		expect(committedPagePath({ type: 'link', to: null }, 200)).toBe(null);
		expect(
			committedPagePath(
				{
					type: 'form',
					from: { url: { pathname: '/team/abc' } },
					to: { url: { pathname: '/team/abc' } }
				},
				200
			)
		).toBe(null);
	});
});

describe('handlePageViewRequest', () => {
	it('forwards a path for a signed-in caller and drops anything else', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		const ok = await handlePageViewRequest(jsonRequest({ path: '/team/abc' }), {
			apiUrl: 'http://api.test/',
			accessToken: 'token',
			fetch
		});
		expect(ok.status).toBe(204);
		const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/me/page-view');
		expect(JSON.parse(String(init.body))).toEqual({ path: '/team/abc' });

		const signedOut = await handlePageViewRequest(jsonRequest({ path: '/team/abc' }), {
			apiUrl: 'http://api.test',
			accessToken: null,
			fetch
		});
		expect(signedOut.status).toBe(204);
		expect(fetch).toHaveBeenCalledOnce();

		const bad = await handlePageViewRequest(jsonRequest({ path: '//evil.test' }), {
			apiUrl: 'http://api.test',
			accessToken: 'token',
			fetch
		});
		expect(bad.status).toBe(400);
		expect(fetch).toHaveBeenCalledOnce();
	});
});

function jsonRequest(body: unknown): { json(): Promise<unknown> } {
	return { json: async () => body };
}
