/**
 * Presentation rules for the Deleted documents listing (`/tenants/[uid]/book/documents`).
 *
 * THE VOCABULARY SPLIT, which is the whole reason this module exists rather than a few inline
 * expressions: the database and the API say `archived` (the `documents.document_status` enum value,
 * the `?status=archived` query parameter), and users say DELETED. Both words are correct in their
 * own layer and neither may leak into the other — renaming the enum would be a migration across a
 * dozen readers, and letting "Archived" onto the screen would contradict the platform portal, which
 * is being relabelled to Deleted. So the translation happens exactly here, once, and
 * `deleted-documents.test.ts` pins that no user-facing string in this section says "archived".
 *
 * The vocabulary is app-local; sizes and dates use `formatBytes`/`formatDate` from `@graphene/ui`.
 */

/** A document's display name. Falls back to its uid: a row with no name is still a real document an
 *  operator may need to quote in a support ticket, so it must not render as a blank cell. */
export function documentName(row: { name: string | null; uid: string }): string {
	return row.name?.trim() || row.uid;
}

/** A document TYPE slug ("suitability_report") as a label ("Suitability report"). Sentence case, not
 *  title case: the slugs are free text a firm coins, so capitalising every word turns a phrase into
 *  a headline. */
export function typeLabel(slug: string): string {
	const words = slug.replace(/[_-]+/g, ' ').trim();
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : '—';
}
