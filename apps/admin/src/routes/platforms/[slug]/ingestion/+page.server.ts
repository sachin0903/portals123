import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/** One file SEI delivered, as its drop ledger recorded it. */
export type DropFile = {
	filename: string;
	report: string;
	drop_date: string | null;
	/** `synced` (fetched and staged) · `present` (already had it) · `failed` (see `error`). */
	status: string;
	bytes: number | null;
	/** SEI's own `Total Number of Records:` trailer. */
	trailer_rows: number | null;
	/** What we actually read. A mismatch is a truncated drop, and fails the file. */
	rows_read: number | null;
	error: string | null;
	processed_at: string | null;
};

export type Ingestion = {
	/** How the data arrives: `sftp` | `webhooks` | `api`. */
	kind: string;
	note: string;
	files: DropFile[];
	/** False when this provider has no per-file ledger — which is a fact about its transport. */
	available: boolean;
	reason?: string;
};

/**
 * How a custodian's data actually arrives, and — where it is a delivery — whether it did.
 *
 * This is the surface that did not exist. SEI's drop ledger records per file whether it synced,
 * was already present, or FAILED, with SEI's own trailer count checked against what was read, so a
 * truncated drop fails that file rather than landing short. None of it reached a screen:
 * `transport: 'sftp'` was a badge on a card, and a night where every file failed looked exactly
 * like a night with no data.
 */
export const load: PageServerLoad = async ({ locals, params, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const provider = params.slug;
	if (!provider) return { ingestion: null, ingestionError: null };
	const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/ingestion`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return { ingestion: null, ingestionError: await detail(res) };
	return { ingestion: (await res.json()) as Ingestion, ingestionError: null };
};
