/**
 * The closed lists on a person, shared by every form and every record.
 *
 * A field of type `title`, `gender`, `marital` or `employment` does not carry its own
 * options. The list here is the one the book stores, which is the values every custodian
 * can hold (`docs/client-vocabularies.md`). Employment is the exception that proves the
 * rule: SEI publishes no employment field, so it does not narrow the eight values Seccl
 * and Morgan Lloyd both accept.
 *
 * The API's tuples in `app/contacts.py` and the onboarding seed's title list are pinned
 * to these arrays. A second handwritten copy is how the fact-find and the record drifted.
 */

export type ClosedOption = { readonly value: string; readonly label: string };

/** Titles every custodian can store. Seccl's word for `Prof` is `Professor`; the send maps it. */
export const TITLE_OPTIONS = [
	{ value: 'Mr', label: 'Mr' },
	{ value: 'Mrs', label: 'Mrs' },
	{ value: 'Miss', label: 'Miss' },
	{ value: 'Ms', label: 'Ms' },
	{ value: 'Dr', label: 'Dr' },
	{ value: 'Prof', label: 'Prof' }
] as const;

export const GENDER_OPTIONS = [
	{ value: 'male', label: 'Male' },
	{ value: 'female', label: 'Female' }
] as const;

export const MARITAL_OPTIONS = [
	{ value: 'single', label: 'Single' },
	{ value: 'married', label: 'Married' },
	{ value: 'divorced', label: 'Divorced' },
	{ value: 'widowed', label: 'Widowed' }
] as const;

export const EMPLOYMENT_OPTIONS = [
	{ value: 'employed', label: 'Employed' },
	{ value: 'self_employed', label: 'Self-employed' },
	{ value: 'retired', label: 'Retired' },
	{ value: 'unemployed', label: 'Unemployed' },
	{ value: 'education', label: 'Education' },
	{ value: 'care_under_16s', label: 'Care under 16s' },
	{ value: 'care_over_16', label: 'Care over 16' },
	{ value: 'other', label: 'Other' }
] as const;

export type BookTitle = (typeof TITLE_OPTIONS)[number]['value'];
export type BookGender = (typeof GENDER_OPTIONS)[number]['value'];
export type BookMaritalStatus = (typeof MARITAL_OPTIONS)[number]['value'];
export type BookEmploymentStatus = (typeof EMPLOYMENT_OPTIONS)[number]['value'];

const CLOSED: Record<string, readonly ClosedOption[]> = {
	title: TITLE_OPTIONS,
	gender: GENDER_OPTIONS,
	marital: MARITAL_OPTIONS,
	employment: EMPLOYMENT_OPTIONS
};

/** The baked-in options for a closed field type, or null when the field authors its own. */
export function closedFieldOptions(fieldType: string): readonly ClosedOption[] | null {
	return CLOSED[fieldType] ?? null;
}

/**
 * The shared list, plus a value already stored that the list does not name.
 *
 * A synced Baroness has to stay visible, or the next save would blank it. A bare number
 * is a custodian code, not a title, so `digitsAreCodes` leaves it off the menu.
 */
export function withStoredChoice(
	options: readonly ClosedOption[],
	current: unknown,
	opts?: { digitsAreCodes?: boolean }
): { value: string; label: string }[] {
	const list = options.map((option) => ({ value: option.value, label: option.label }));
	const value = String(current ?? '').trim();
	if (!value || list.some((option) => option.value === value)) return list;
	if (opts?.digitsAreCodes && /^[0-9]+$/.test(value)) return list;
	return [...list, { value, label: value }];
}
