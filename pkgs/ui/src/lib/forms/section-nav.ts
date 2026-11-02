import type { FormField } from './types.js';

/** About one laptop screen of fields. A form that opens with fewer is quicker to scroll than to
 *  navigate, and a list of its sections only takes room from it. */
export const SECTION_NAV_MIN_FIELDS = 12;

/**
 * Whether a form is long enough for a section nav (the rail, or the dropdown that replaces it on a
 * narrow screen): more than one section, and at least `SECTION_NAV_MIN_FIELDS` fields showing when
 * it opens. Counted from the form as it OPENS — declared fields, optional sections at their default
 * — never from the answers or the switches, so the nav does not come and go while it is filled in.
 * Hidden fields are derived sidecars that render nothing, so they count for neither.
 */
export function wantsSectionNav(
	fields: Pick<FormField, 'section' | 'validation'>[],
	sectionMeta: Record<string, { optional?: boolean; defaultOn?: boolean }> = {}
): boolean {
	const shown = fields.filter((f) => !f.validation?.hidden);
	const sectionOf = (f: (typeof shown)[number]) => f.section || 'Additional information';
	if (new Set(shown.map(sectionOf)).size < 2) return false;
	const opensOn = (title: string) => {
		const meta = sectionMeta[title];
		return !meta?.optional || (meta.defaultOn ?? false);
	};
	return shown.filter((f) => opensOn(sectionOf(f))).length >= SECTION_NAV_MIN_FIELDS;
}
