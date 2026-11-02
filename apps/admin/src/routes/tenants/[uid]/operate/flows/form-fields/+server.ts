import { error, json } from '@sveltejs/kit';
import { targetJson } from '$lib/operate';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, locals, fetch, params }) => {
	const uid = url.searchParams.get('uid') ?? '';
	if (!uid) error(400, 'No form specified.');
	const result = await targetJson<unknown[]>(
		fetch,
		locals.accessToken,
		params.uid,
		`/forms/${uid}/fields`
	);
	if (!result.ok && result.status !== 404) error(502, 'Could not load this form’s questions.');
	return json(
		result.ok
			? { fields: result.data, permitted: true, missing: false }
			: { fields: [], permitted: true, missing: result.status === 404 }
	);
};
