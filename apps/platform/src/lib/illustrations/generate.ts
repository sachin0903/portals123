// The generate call made by the illustration form, which opens as a drawer on the client record.
// Kept out of the drawer so the thing that matters most here — how a partial success is reported —
// is stated once, testable, and not buried in a component.
//
// "Produced" and "filed" are separate outcomes, and so is "placeholder". An illustration that reads
// as a quotation and is not one is the single failure this feature must never produce quietly, so
// the caller gets all three flags rather than a boolean.

import { toIllustrationPayload } from '$lib/illustrations/form';

/** What `POST /clients/{uid}/generate-illustration` answers with. */
export interface GenerateResult {
	illustration_uid?: string | null;
	document_uid: string | null;
	stored: boolean;
	placeholder: boolean;
	placeholder_reason: string | null;
	request_xml: string | null;
	errors: string[];
}

export type GenerateOutcome =
	| { ok: true; result: GenerateResult }
	/** The request never reached the engine, or was refused before it did. Correct and retry. */
	| { ok: false; errors: string[] };

/**
 * Build the CTC1 body from the form's answers and post it.
 *
 * `values` is what `DynamicForm.collect()` returned, so a switched-off section is already absent
 * and `toIllustrationPayload` reports it as "no such stream" rather than as zero.
 */
export async function generateIllustration(
	contactUid: string,
	values: Record<string, unknown>,
	options: {
		fetcher?: typeof globalThis.fetch;
		/** The client's other SIPP accounts, summed. Null when they have none. */
		existingSippTotal?: number | null;
	} = {}
): Promise<GenerateOutcome> {
	const fetcher = options.fetcher ?? fetch;
	try {
		const res = await fetcher(`/clients/${contactUid}/generate-illustration`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(toIllustrationPayload(values, options.existingSippTotal ?? null))
		});
		const body = await res.json();
		if (!res.ok) {
			return { ok: false, errors: [body.error ?? 'Could not generate the illustration.'] };
		}
		return { ok: true, result: body as GenerateResult };
	} catch {
		return { ok: false, errors: ['Could not reach the illustration service.'] };
	}
}

/** Where a completed quotation should open on the client record.

    Indicative output and output carrying engine errors stay in the outcome panel even when filed:
    that panel is where the adviser is warned not to issue an indicative document and can inspect
    the errors. The filed document remains linked from there. */
export function filedDocumentPath(contactUid: string, result: GenerateResult): string | null {
	if (!result.stored || !result.document_uid || result.placeholder || result.errors.length)
		return null;
	return `/clients/${contactUid}/documents/${result.document_uid}`;
}

/** The one-line summary of an outcome, and its tone. Named here so the drawer's headline and its
    toast describe the same result the same way. */
export function describeResult(result: GenerateResult): {
	tone: 'success' | 'warning' | 'danger';
	label: string;
	headline: string;
} {
	if (!result.stored) {
		return { tone: 'danger', label: 'Not filed', headline: 'Produced, but not filed' };
	}
	if (result.placeholder) {
		return { tone: 'warning', label: 'Indicative', headline: 'Indicative illustration filed' };
	}
	return { tone: 'success', label: 'Illustration', headline: 'Filed against the client' };
}
