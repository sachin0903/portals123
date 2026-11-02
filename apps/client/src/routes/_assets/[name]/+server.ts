import { env } from '$env/dynamic/private';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

/** Same-origin relay for branding and CMS images held on the internal-only object store. */
export const GET: RequestHandler = async ({ params, request, fetch }) => {
	const api = env.API_URL ?? '';
	const token = env.INTERNAL_API_TOKEN ?? '';
	if (!api || !token) error(404, 'Not found');

	const host = request.headers.get('host') ?? '';
	const upstream = await fetch(
		`${api}/internal/assets/${encodeURIComponent(params.name)}?host=${encodeURIComponent(host)}`,
		{ headers: { 'x-internal-token': token } }
	);
	if (!upstream.ok) error(upstream.status === 404 ? 404 : 502, 'Asset unavailable');

	return new Response(upstream.body, {
		headers: {
			'content-type': upstream.headers.get('content-type') ?? 'application/octet-stream',
			'cache-control':
				upstream.headers.get('cache-control') ?? 'public, max-age=31536000, immutable',
			'content-security-policy':
				upstream.headers.get('content-security-policy') ??
				"default-src 'none'; style-src 'unsafe-inline'; sandbox",
			'x-content-type-options': 'nosniff'
		}
	});
};
