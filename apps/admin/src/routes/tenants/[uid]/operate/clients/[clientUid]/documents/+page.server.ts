import { redirect } from '@sveltejs/kit';
import { fileToRow, toRow, type ApiDocument, type ApiFile } from '@graphene/ui/book/documents';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, detail } = await parent();
	const [documents, files] = await Promise.all([
		targetJson<ApiDocument[]>(fetch, locals.accessToken, tenant.uid, '/documents', {
			contact_id: detail.contact.id
		}),
		targetJson<ApiFile[]>(fetch, locals.accessToken, tenant.uid, '/files', {
			contact_id: detail.contact.id
		})
	]);
	return {
		documents: [
			...(documents.ok ? documents.data.map(toRow) : []),
			...(files.ok ? files.data.map(fileToRow) : [])
		],
		loadError: !documents.ok || !files.ok
	};
};
