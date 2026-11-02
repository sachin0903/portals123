/**
 * An account's SERVICE TYPE — the service the firm provides on it: advisory, discretionary or
 * execution-only.
 *
 * The value is decided by the API (`crm.py`'s account SELECTs: the captured
 * `platform.accounts.investment_capacity`, falling back to `discretionary` for a model-run
 * account, and NULL when neither says). Nothing here re-derives it from `model_id`, because the
 * listing FILTERS on the same field server-side — a second rule for what "discretionary" means
 * would show one answer in the column and select a different set in the filter.
 *
 * Two vocabularies reach this field and both are real: onboarding stores `advisory`
 * (`flows/step_handlers.py`), while `crm.contacts.service_type` — the same question asked of the
 * PERSON — is an enum whose member is `advised` (0002, widened by 0104). They are one service, so
 * they get one label.
 */

/** The label an adviser reads, or `null` when the value is unset or not one we know. */
export function serviceTypeLabel(value: string | null | undefined): string | null {
	switch ((value ?? '').trim().toLowerCase()) {
		// `advised` is `crm.contacts.service_type`'s own spelling of the same service (a comment
		// BETWEEN the two labels reads as an unintended fallthrough to eslint, hence up here).
		case 'advisory':
		case 'advised':
			return 'Advisory';
		case 'discretionary':
			return 'Discretionary';
		case 'execution_only':
			return 'Execution only';
		default:
			return null;
	}
}

/**
 * The filter's options, in the order a firm describes its own service ladder — most advice first.
 *
 * A FIXED list, not derived from the rows on screen: the three are the whole vocabulary, and a
 * facet built from one page could only offer what happened to be visible.
 */
export const SERVICE_TYPE_OPTIONS: { value: string; label: string }[] = [
	{ value: 'discretionary', label: 'Discretionary' },
	{ value: 'advisory', label: 'Advisory' },
	{ value: 'execution_only', label: 'Execution only' }
];
