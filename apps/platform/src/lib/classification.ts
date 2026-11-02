import { apiUrl } from '$lib/api';

/** A client book, as `GET /crm/classifications` lists them — the pin menu's options. */
export type ClientBook = { uid: string; name: string; slug: string };

/** The book this client resolves to and how (`GET /crm/contacts/{uid}/classification`). */
export type ResolvedClientBook = ClientBook & { source: 'pinned' | 'matched' | 'default' };

export type ClientBookPin = {
	classifications: ClientBook[];
	resolvedClassification: ResolvedClientBook | null;
};

/**
 * The client-book pin for one record: the firm's books and the one this client resolves to.
 *
 * Read by the two sections that render it (Overview and Details), INSIDE their streamed record,
 * not by the record's layout. It used to be a second awaited stretch in `clients/[uid]/+layout.server.ts`
 * behind the shell, so every section of the record — transactions, documents, illustrations —
 * paid a round trip for a menu only two of them draw, and paid it before the first byte.
 *
 * Both reads are best-effort: a failure is an empty menu and no badge, never a failed record.
 * The list is only asked for when the caller may read contacts; the resolution is always asked
 * for, as before.
 */
export async function loadClientBookPin(
	uid: string,
	headers: HeadersInit,
	fetch: typeof globalThis.fetch,
	{ canListBooks }: { canListBooks: boolean }
): Promise<ClientBookPin> {
	const [classRes, resolvedRes] = await Promise.all([
		canListBooks ? fetch(`${apiUrl()}/crm/classifications`, { headers }) : null,
		fetch(`${apiUrl()}/crm/contacts/${uid}/classification`, { headers })
	]);
	const listed: unknown = classRes?.ok ? await classRes.json() : [];
	const resolved = resolvedRes.ok
		? ((await resolvedRes.json()) as { classification: ResolvedClientBook | null })
		: { classification: null };
	return {
		classifications: Array.isArray(listed) ? (listed as ClientBook[]) : [],
		resolvedClassification: resolved.classification ?? null
	};
}
