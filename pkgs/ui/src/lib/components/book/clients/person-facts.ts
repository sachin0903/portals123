/**
 * A person's compliance facts, as the client record and the compliance tab both show them: the
 * US-person answer, the W-9 or W-8 on file and its signed form, source of funds and source of wealth, each with its
 * detail when the answer is "Other". A trust or a company has none of them: they are facts about a
 * person. An unanswered fact is null, which both pages print as an em dash, so a firm sees the
 * question exists.
 */
import type { ClientContact } from './types.js';

export type PersonFact = [label: string, value: string | null];

const label = (v: string) => v.replace(/_/g, ' ').replace(/^./, (ch) => ch.toUpperCase());

/** W-9 and W-8 by name; any other stored code reads as words. */
export const fatcaFormLabel = (v: string) => (v === 'w9' ? 'W-9' : v === 'w8' ? 'W-8' : label(v));

/** Only a W-9 or a W-8 takes a signed form. Other stored codes are SEI's and carry no file. */
export const takesFatcaDocument = (v: string | null | undefined): boolean =>
	v === 'w9' || v === 'w8';

/** The signed form's row. The client record keys on it to link the file. */
export const FATCA_DOCUMENT_FACT = 'W-9 / W-8 form';

export function personComplianceFacts(
	c: Pick<
		ClientContact,
		| 'us_person'
		| 'fatca_status'
		| 'fatca_document'
		| 'source_of_funds'
		| 'source_of_funds_detail'
		| 'source_of_wealth'
		| 'source_of_wealth_detail'
	>
): PersonFact[] {
	return [
		['Is a US person?', c.us_person == null ? null : c.us_person ? 'Yes' : 'No'],
		['W-9 or W-8', c.fatca_status ? fatcaFormLabel(c.fatca_status) : null],
		...(takesFatcaDocument(c.fatca_status)
			? ([[FATCA_DOCUMENT_FACT, c.fatca_document?.name ?? 'Not uploaded']] as PersonFact[])
			: []),
		['Source of funds', c.source_of_funds ? label(c.source_of_funds) : null],
		...(c.source_of_funds === 'Other (please specify)'
			? ([['Detail', c.source_of_funds_detail ?? null]] as PersonFact[])
			: []),
		['Source of wealth', c.source_of_wealth ? label(c.source_of_wealth) : null],
		...(c.source_of_wealth === 'Other'
			? ([['Detail', c.source_of_wealth_detail ?? null]] as PersonFact[])
			: [])
	];
}
