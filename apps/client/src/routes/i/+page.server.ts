/**
 * The photo page the phone link opens. No sign-in: the cookie is the session the code was
 * exchanged for, and it can send these photos and nothing else.
 */

import { fail } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { IDENTITY_CAPTURE_KEYS } from '$lib/identity-capture';
import { loadPhoneCapture, reportPhoneTaken, submitPhoneCapture } from '$lib/server/identity-phone';
import { IDV_PHONE_COOKIE } from '$lib/server/workos';
import type { Actions, PageServerLoad } from './$types';

function tokenOf(cookies: { get: (name: string) => string | undefined }): string {
	return cookies.get(IDV_PHONE_COOKIE) ?? '';
}

function service(fetch: typeof globalThis.fetch) {
	return { internalToken: env.INTERNAL_API_TOKEN ?? '', fetch };
}

export const load: PageServerLoad = async ({ cookies, fetch }) => {
	const token = tokenOf(cookies);
	if (!token) return { capture: null, unavailable: false };
	const loaded = await loadPhoneCapture(token, service(fetch));
	if (loaded.ok) return { capture: loaded.capture, unavailable: false };
	if (loaded.ended) cookies.delete(IDV_PHONE_COOKIE, { path: '/i' });
	return { capture: null, unavailable: !loaded.ended };
};

export const actions: Actions = {
	/** What the phone has taken so far: no photo, only which ones, for the waiting computer. */
	taken: async ({ request, cookies, fetch }) => {
		const token = tokenOf(cookies);
		if (!token) return fail(401, { error: 'This link has already been used or has expired.' });
		const form = await request.formData();
		const keys = form.getAll('key').map(String);
		const documentType = String(form.get('document_type') ?? '') || null;
		await reportPhoneTaken(token, keys, documentType, service(fetch));
		return { ok: true };
	},

	submit: async ({ request, cookies, fetch }) => {
		const token = tokenOf(cookies);
		if (!token) return fail(401, { error: 'This link has already been used or has expired.' });
		const form = await request.formData();
		const documentType = String(form.get('document_type') ?? '');
		if (!['passport', 'driving_licence', 'national_id'].includes(documentType)) {
			return fail(400, { error: 'Choose the document you photographed.' });
		}
		const body = new FormData();
		body.append('document_type', documentType);
		for (const photo of IDENTITY_CAPTURE_KEYS) {
			const file = form.get(photo);
			if (file instanceof File && file.size > 0) body.append(photo, file, file.name);
		}
		const error = await submitPhoneCapture(token, body, service(fetch));
		if (error) return fail(400, { error });
		return { ok: true };
	}
};
