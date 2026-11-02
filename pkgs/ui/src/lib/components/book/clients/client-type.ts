/**
 * What KIND of client this is — an individual, or an organisation of a particular kind.
 *
 * `crm.contacts` says it in two columns and 0002's CHECK ties them together: `type` is
 * `individual | organisation`, and `sub_type` (`trust | company | charity`) may only be set on an
 * organisation. So the specific answer is the sub-type when there is one and the base type
 * otherwise, which is what the API publishes as `client_type` — decided there, once, for the same
 * reason `service_type` is: the listing FILTERS on it server-side, and a second rule for what
 * "organisation" covers would show one answer in the column and select a different set.
 *
 * The two levels are a HIERARCHY, not alternatives. The filter matches either, so "organisation"
 * still finds a trust while "trust" narrows to it.
 */

/** The label an adviser reads, or `null` for a value the vocabulary does not know. */
export function clientTypeLabel(value: string | null | undefined): string | null {
	switch ((value ?? '').trim().toLowerCase()) {
		case 'individual':
			return 'Individual';
		case 'organisation':
			return 'Organisation';
		case 'company':
			return 'Company';
		case 'trust':
			return 'Trust';
		case 'charity':
			return 'Charity';
		default:
			return null;
	}
}

/**
 * The kinds of organisation a firm may record against a client, for the record's own picker.
 *
 * `crm.contact_sub_type`'s whole vocabulary, and deliberately a FIXED list rather than a facet:
 * this is what may be CHOSEN, where a facet can only offer what the book already contains — a
 * firm's first trust would otherwise be unrecordable.
 */
export const ORGANISATION_SUB_TYPES: { value: string; label: string }[] = [
	{ value: 'company', label: 'Company' },
	{ value: 'trust', label: 'Trust' },
	{ value: 'charity', label: 'Charity' }
];
