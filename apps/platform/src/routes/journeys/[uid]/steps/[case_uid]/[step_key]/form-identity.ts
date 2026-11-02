/**
 * The identity that remounts the staff step form when the adviser walks Prev/Next.
 *
 * Two applicants on one journey share a form template and a step key (`applicant_core` /
 * `core`). Keying only those two left SvelteKit's reused page holding applicant 1's
 * in-memory values, which then submitted against applicant 2's request item.
 * `case_uid` distinguishes profile cases; `participant` distinguishes fanned instances
 * inside one case.
 */
export function stepFormIdentity(
	caseUid: string,
	stepKey: string,
	participant: string | null | undefined
): string {
	return `${caseUid}:${stepKey}:${participant ?? ''}`;
}
