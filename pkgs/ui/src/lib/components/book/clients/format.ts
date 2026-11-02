import { formatDate } from '../../../dates.js';

/** The provider prefixes `crm.contacts.uid`/`platform.accounts.uid` are actually stored with —
 *  `apps/api/scripts/ingest_seccl.py` writes e.g. `seccl_CXF421C15` because the column is
 *  globally UNIQUE with no per-provider scoping, so the prefix is the only thing stopping two
 *  providers (or two tenants on the same provider) from colliding on it. Real, not decorative —
 *  do NOT strip it from the stored value; this list is for DISPLAY only. */
const PROVIDER_UID_PREFIXES = ['seccl_', 'sei_', 'ml_'];

/** A client/account reference as a human should read it: the provider's own id, with the
 *  provider's namespacing prefix (see PROVIDER_UID_PREFIXES) dropped for display. `platform.
 *  accounts.uid` is nullable (unlike `crm.contacts.uid`), so this passes null/undefined through
 *  rather than widening every call site into its own null check. */
export function providerReference(uid: string | null | undefined): string | null | undefined {
	if (!uid) return uid;
	const prefix = PROVIDER_UID_PREFIXES.find((p) => uid.startsWith(p));
	return prefix ? uid.slice(prefix.length) : uid;
}

/** A person/entity's display name, org-aware. Shared across every book surface (the platform's
 *  `$lib/format` re-exports it), which is why it lives with the shared clients components. */
export function clientName(c: {
	type: 'individual' | 'organisation';
	name?: string | null;
	title?: string | null;
	first_name?: string | null;
	surname?: string | null;
	uid?: string | null;
}): string {
	if (c.type === 'organisation') return c.name ?? providerReference(c.uid) ?? 'Organisation';
	const full = [c.title, c.first_name, c.surname].filter(Boolean).join(' ').trim();
	return full || c.name || providerReference(c.uid) || 'Client';
}

/** Where a client is, in a listing's second line: town, postcode and country, the record
 *  header's address line without its street. Blank parts are dropped rather than printed as
 *  empty commas, so a client with only a postcode reads as a postcode. Null when nothing of the
 *  three is stored — the caller decides what, if anything, stands in. The stored object is typed
 *  loosely because `crm.contacts.address` is jsonb and older rows carry whatever the form of the
 *  day wrote. */
export function shortLocation(address: unknown): string | null {
	const row = address as Record<string, unknown> | null | undefined;
	if (!row || typeof row !== 'object') return null;
	const parts = [row.city, row.postcode, row.country]
		.map((part) => String(part ?? '').trim())
		.filter(Boolean);
	return parts.length ? parts.join(', ') : null;
}

/** The line under a client's name in a listing: their date of birth (`5 Aug 1962`, as the record
 *  prints it), else where they are, else nothing. The date is what tells two Alan Hornes apart
 *  and what a custodian asks for first; an organisation has no birthday, so it gets its town.
 *  One function so the clients page and the dashboard cannot disagree about the fallback. */
export function clientSubline(c: { dob?: string | null; address?: unknown }): string | null {
	const dob = formatDate(c.dob, '');
	return dob || shortLocation(c.address);
}
