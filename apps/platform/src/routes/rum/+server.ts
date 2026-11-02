import { handleRumRequest } from '@graphene/rum/forward';
import { apiUrl } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser page-view vitals. The portal forwards them; the browser never calls the API. */
export const POST: RequestHandler = ({ request, locals }) =>
	handleRumRequest(request, { apiUrl: apiUrl(), accessToken: locals.accessToken });
