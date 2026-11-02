import type { FormField } from '../types.js';

// Pure helpers behind the forms system's declarative-value features, kept out of the Svelte
// component so they can be unit-tested directly (like validate.ts / visibility.ts / options.ts):
//  • `display` fields render a read-only, record-derived value rather than an input;
//  • `validation.defaultValue` seeds a field once;
//  • `validation.computedFrom` fills a field from a data-source row keyed by another field's value.
// All new config lives in the opaque `validation` jsonb, so it persists with no backend change.

/** A field renders a read-only value rather than an editable control. */
export function isDisplayField(field: Pick<FormField, 'fieldType'>): boolean {
	return field.fieldType === 'display';
}

/** Values to seed for fields that declare a `validation.defaultValue` and have no current answer.
 *  Returns a patch to merge into the form's values. Never overwrites a defined answer, so it seeds
 *  once on first render and then leaves the user's input alone. */
export function pendingDefaults(
	fields: FormField[],
	values: Record<string, unknown>
): Record<string, unknown> {
	const patch: Record<string, unknown> = {};
	for (const field of fields) {
		const dv = field.validation?.defaultValue;
		if (dv === undefined) continue;
		if (values[field.fieldKey] === undefined) patch[field.fieldKey] = dv;
	}
	return patch;
}

/** Resolve every `validation.computedFrom` field against the current values + data sources. Returns
 *  a patch of `{ fieldKey: value }`: the matched row's `valueKey`, or `undefined` when no row matches
 *  (so clearing the match field clears the computed value). Matching compares as strings, so a
 *  string select value (e.g. `"12"`) matches a numeric row id (`12`). */
export function computedValues(
	fields: FormField[],
	values: Record<string, unknown>,
	dataSources: Record<string, unknown[]>
): Record<string, unknown> {
	const patch: Record<string, unknown> = {};
	for (const field of fields) {
		const cf = field.validation?.computedFrom;
		if (!cf) continue;
		const rows = dataSources[cf.source] ?? [];
		const target = values[cf.matchOn];
		const matchKey = cf.matchKey ?? 'id';
		const row = rows.find(
			(r) =>
				r != null &&
				typeof r === 'object' &&
				String((r as Record<string, unknown>)[matchKey]) === String(target)
		);
		patch[field.fieldKey] = row ? (row as Record<string, unknown>)[cf.valueKey] : undefined;
	}
	return patch;
}

/**
 * The DOM id of a field's control.
 *
 * Shared because two files need to agree on it: `dynamic-field.svelte` stamps it, and
 * `dynamic-form.svelte` reads it back to move the adviser to the first field that failed
 * validation. A second spelling of `fld-${uid}` would fail silently — `getElementById` would
 * simply find nothing and the jump would quietly do nothing at all.
 */
export function fieldControlId(uid: string): string {
	return `fld-${uid}`;
}

/** A stable DOM id for a form section, so a section nav can anchor + scroll-spy to it. */
export function sectionAnchorId(section: string): string {
	const slug = section
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
	return `form-section-${slug || 'section'}`;
}
