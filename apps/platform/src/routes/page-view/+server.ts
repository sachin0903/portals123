import { handlePageViewRequest } from '@graphene/auth';
import { apiUrl } from '$lib/api';
import type { RequestHandler } from './$types';

/** Committed client navigations. The document load is counted in the server hook. */
export const POST: RequestHandler = ({ request, locals }) =>
	handlePageViewRequest(request, { apiUrl: apiUrl(), accessToken: locals.accessToken });
