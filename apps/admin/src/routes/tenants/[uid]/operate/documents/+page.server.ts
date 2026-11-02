import { fail, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { apiParams, itemsOf, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import {
	fileToRow,
	toRow,
	type ApiDocument,
	type ApiFile,
	type ClientOption,
	type DocumentTypeOption
} from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

const FILTER_KEYS = ['type', 'author', 'client', 'size', 'created'] as const;
// NO fallback sort here: `GET /documents` takes no `sort`/`dir` — a key here would be dropped.
const CAP = 200;

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const query = { ...queryFromParams(url.searchParams, FILTER_KEYS, CAP), offset: 0 };
	const params = apiParams(query);
	const [documents, files, clients, types, activity] = await Promise.all([
		targetJson<ListingPage<ApiDocument> | ApiDocument[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/documents',
			params
		),
		targetJson<ListingPage<ApiFile> | ApiFile[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/files',
			params
		),
		writing
			? targetJson<ClientOption[]>(fetch, locals.accessToken, tenant.uid, '/documents/clients')
			: null,
		writing
			? targetJson<DocumentTypeOption[]>(fetch, locals.accessToken, tenant.uid, '/documents/types')
			: null,
		targetJson<{ items: ActionEvent[] }>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/firm/audit/actions',
			{ document_family: true, limit: 25 }
		)
	]);
	const docs = documents.ok ? itemsOf(documents.data) : [];
	const supporting = files.ok ? itemsOf(files.data) : [];
	const docsPage = documents.ok && !Array.isArray(documents.data) ? documents.data : null;
	const filesPage = files.ok && !Array.isArray(files.data) ? files.data : null;
	const facets: Record<string, string[]> = {};
	for (const source of [docsPage?.facets ?? {}, filesPage?.facets ?? {}]) {
		for (const [key, values] of Object.entries(source)) {
			facets[key] = [...new Set([...(facets[key] ?? []), ...values])];
		}
	}
	return {
		documents: [...docs.map(toRow), ...supporting.map(fileToRow)],
		clients: clients?.ok ? clients.data : [],
		documentTypes: types?.ok ? types.data : [],
		activity: activity.ok ? activity.data.items : [],
		activityFailed: !activity.ok,
		loadError: !documents.ok || !files.ok,
		total: (docsPage?.total ?? docs.length) + (filesPage?.total ?? supporting.length),
		facets,
		facetLabels: { ...(docsPage?.facet_labels ?? {}), ...(filesPage?.facet_labels ?? {}) },
		query,
		writing
	};
};

async function requireWrite(event: Parameters<NonNullable<Actions[keyof Actions]>>[0]) {
	return canOperateWrite(event.locals) ? { tenant: { uid: event.params.uid } } : null;
}

export const actions: Actions = {
	upload: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const form = await event.request.formData();
		const files = form.getAll('file').filter((value): value is File => value instanceof File);
		if (!files.length) return fail(400, { error: 'Choose at least one file.' });
		const body = new FormData();
		for (const file of files) body.append('file', file, file.name);
		for (const key of ['name', 'document_type', 'subject_contact_id']) {
			const value = String(form.get(key) ?? '').trim();
			if (value) body.append(key, value);
		}
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			'/documents/upload',
			{ method: 'POST', body }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	uploadFile: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const form = await event.request.formData();
		const files = form.getAll('file').filter((value): value is File => value instanceof File);
		if (!files.length) return fail(400, { error: 'Choose at least one file.' });
		for (const file of files) {
			const body = new FormData();
			body.append('file', file, file.name);
			body.append('name', file.name);
			body.append('attachment_type', String(form.get('attachment_type') ?? ''));
			const contactId = String(form.get('contact_id') ?? '');
			if (contactId) body.append('contact_id', contactId);
			const response = await targetMutation(
				event.fetch,
				event.locals.accessToken,
				parent.tenant.uid,
				'/files/upload',
				{ method: 'POST', body }
			);
			if (!response.ok) return fail(response.status, { error: await detail(response) });
		}
		return { ok: true };
	},
	classify: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const filename = String((await event.request.formData()).get('filename') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			'/documents/classify-type',
			{ method: 'POST', body: JSON.stringify({ filename }) }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return await response.json();
	},
	archive: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			`/documents/${uid}/archive`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	unarchive: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			`/documents/${uid}/unarchive`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	retryRender: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			`/documents/${uid}/retry`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	deleteFile: async (event) => {
		const parent = await requireWrite(event);
		if (!parent) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			parent.tenant.uid,
			`/files/${uid}`,
			{ method: 'DELETE' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	}
};
