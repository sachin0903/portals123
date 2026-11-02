import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';

/**
 * Whether this caller may dump a book listing to a file.
 *
 * Reading a page of clients is day-to-day work; exporting the match set is taking the book
 * off-platform. The slug is `listings:export` — a named action, not implied by `contacts:read`
 * or `contacts:write`.
 */
export function canExportListings(
	permissions: Iterable<string> | null | undefined,
	role: string | null | undefined
): boolean {
	return can(permissions ?? [], role ?? null, 'listings:export');
}

/**
 * The listing export URL, or `undefined` when the caller may not export — which is what hides
 * the Export button (`ExportMenu` only renders when `exportHref` is set).
 */
export function listingExportHref(
	permissions: Iterable<string> | null | undefined,
	role: string | null | undefined,
	href: string
): string | undefined {
	return canExportListings(permissions, role) ? href : undefined;
}

/**
 * Gate an export `+server.ts`. Login redirect stays the caller's job: this only answers
 * "may they take the book off-platform", and a missing grant is 403 rather than a login bounce
 * so a crafted download is refused as a permission, not as an expired session.
 */
export function requireListingExport(locals: {
	permissions?: Iterable<string> | null;
	role?: string | null;
}): void {
	if (!canExportListings(locals.permissions, locals.role)) {
		error(403, 'Not permitted');
	}
}
