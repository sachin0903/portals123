import { error, fail, redirect } from '@sveltejs/kit';
import { IDENTITY_DOCUMENT_TYPES, IDENTITY_PHOTO_FIELDS } from '@graphene/ui';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { clientHref, type AmlCheckDetail, type AmlClientPage } from '$lib/aml';
import { can } from '$lib/permissions';
import {
	allowJourney,
	decideCheck,
	decideMatch,
	monitoring,
	requestPhotos,
	rerun,
	screen,
	send
} from '$lib/server/aml';
import type { Actions, PageServerLoad } from './$types';

const DOCUMENT_TYPES: string[] = IDENTITY_DOCUMENT_TYPES.map((d) => d.value);

export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');

	let res: Response;
	try {
		res = await fetch(`${apiUrl()}/aml/clients/${encodeURIComponent(params.key)}`, {
			headers: authHeaders(locals.accessToken)
		});
	} catch {
		error(503, 'The service is unavailable.');
	}
	if (res.status === 404) error(404, 'Client not found.');
	if (!res.ok) error(res.status, await detail(res));
	const page: AmlClientPage = await res.json();

	const focus = url.searchParams.get('check');
	// A contact key outlives the client's first screen; the page moves to the lasting key.
	if (page.key !== params.key) redirect(303, clientHref(page.key, focus));

	// One earlier check opened in full, with every act on it. Only this client's own: a uid from
	// anywhere else is ignored rather than shown under the wrong name.
	let earlier: AmlCheckDetail | null = null;
	let earlierError: string | null = null;
	if (focus && page.earlier.some((check) => check.uid === focus)) {
		try {
			const found = await fetch(`${apiUrl()}/aml/checks/${encodeURIComponent(focus)}`, {
				headers: authHeaders(locals.accessToken)
			});
			if (found.ok) earlier = await found.json();
			else earlierError = await detail(found);
		} catch {
			earlierError = 'The service is unavailable.';
		}
	}

	return {
		page,
		earlier,
		earlierError,
		canWrite: can(locals.permissions, locals.role, 'aml:write'),
		// aml:adjudicate exists only in the operations portal: only Graphene confirms or dismisses a
		// match and clears or rejects a check.
		canAdjudicate: can(locals.permissions, locals.role, 'aml:adjudicate')
	};
};

// The acts on a check are shared (`$lib/server/aml`): any page that shows a check's sections
// posts the same ones. Only the acts on the client as a whole are this page's own.
export const actions: Actions = {
	/** Confirm or dismiss one match (shared with the workbench). */
	decideMatch,
	allowJourney,
	decideCheck,
	requestPhotos,

	/**
	 * Upload new identity photos for the client, on their behalf, and start a new identity check
	 * from them. A photo left out is kept: the API uses the document or selfie ComplyCube holds for
	 * the client. The comment is internal — never shown to the client or the firm.
	 */
	uploadPhotos: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'aml:adjudicate')) error(403, 'Not authorised');

		const form = await request.formData();
		const photos = IDENTITY_PHOTO_FIELDS.flatMap((field) => {
			const file = form.get(field);
			return file instanceof File && file.size > 0 ? [{ field, file }] : [];
		});
		const documentSent = photos.some((photo) => photo.field !== 'idv_selfie');
		const documentType = String(form.get('document_type') ?? '').trim();
		const country = String(form.get('issuing_country') ?? '')
			.trim()
			.toUpperCase();
		// A textarea posts its line breaks as CRLF; the comment keeps plain newlines.
		const note = String(form.get('comment') ?? '')
			.replace(/\r\n?/g, '\n')
			.trim();

		if (!photos.length)
			return fail(400, { action: 'uploadPhotos', error: 'Add at least one photo.' });
		if (documentSent && !DOCUMENT_TYPES.includes(documentType))
			return fail(400, { action: 'uploadPhotos', error: 'Choose the document type.' });
		if (!note) return fail(400, { action: 'uploadPhotos', error: 'Add a note for the record.' });

		// The document's type and country go only with a side of it: a selfie alone keeps the
		// document ComplyCube holds.
		const body = new FormData();
		if (documentSent) {
			body.append('document_type', documentType);
			if (country) body.append('issuing_country', country);
		}
		body.append('comment', note);
		for (const { field, file } of photos) body.append(field, file, file.name);

		let res: Response;
		try {
			// No content-type: fetch sets the multipart boundary.
			res = await fetch(
				`${apiUrl()}/aml/clients/${encodeURIComponent(params.key)}/identity-photos`,
				{ method: 'POST', headers: authHeaders(locals.accessToken), body }
			);
		} catch {
			return fail(503, { action: 'uploadPhotos', error: 'The service is unavailable.' });
		}
		if (!res.ok) return fail(res.status, { action: 'uploadPhotos', error: await detail(res) });
		return { action: 'uploadPhotos' };
	},

	monitoring,

	/** Add an internal note on the client. Graphene only: the firm never sees it. */
	addNote: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'aml:write')) error(403, 'Not authorised');

		const form = await request.formData();
		// A textarea posts its line breaks as CRLF; the note keeps plain newlines.
		const body = String(form.get('body') ?? '')
			.replace(/\r\n?/g, '\n')
			.trim();
		if (!body) return fail(400, { action: 'addNote', error: 'Write a note.' });
		const res = await send(
			fetch,
			locals.accessToken,
			`/aml/clients/${encodeURIComponent(params.key)}/notes`,
			'POST',
			{ body }
		);
		if (!res) return fail(503, { action: 'addNote', error: 'The service is unavailable.' });
		if (!res.ok) return fail(res.status, { action: 'addNote', error: await detail(res) });
		return { action: 'addNote' };
	},

	rerun,
	screen
};
