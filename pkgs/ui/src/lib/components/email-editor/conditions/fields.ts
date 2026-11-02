/** DERIVED from the template's own merge vocabulary (`merge-fields.ts`, per code): a field the send site
 *  never passes is a rule silently always false, hiding a block from everyone. Tokens in, bare refs out. */
import type { SelectMenuOption } from '../../ui/select-menu/index.js';
import type { ConditionOnlyField, MergeFieldGroup } from '../merge-fields.js';
import { jinjaField } from './model.js';

/** Null for a token carrying an EXPRESSION: `{{ x or 'there' }}` is never falsey, so nothing to ask. */
export function fieldFromToken(token: string): string | null {
	const inner = /^\s*\{\{(.+?)\}\}\s*$/.exec(token ?? '')?.[1];
	return inner === undefined ? null : jinjaField(inner);
}

/** `extra` is the askable-but-not-printable half (`conditionOnlyFieldsFor`): a boolean or a URL. */
export function fieldOptions(
	groups: readonly MergeFieldGroup[],
	extra: readonly ConditionOnlyField[] = []
): SelectMenuOption[] {
	const seen = new Set<string>();
	const out: SelectMenuOption[] = [];
	for (const group of groups) {
		for (const field of group.fields) {
			const value = fieldFromToken(field.token);
			if (!value || seen.has(value)) continue;
			seen.add(value);
			// The author's word AND the reference, because the tab and the compiled `{% if %}` show the reference.
			out.push({ value, label: `${field.label} · ${value}` });
		}
	}
	for (const field of extra) {
		if (seen.has(field.field)) continue;
		seen.add(field.field);
		out.push({ value: field.field, label: `${field.label} · ${field.field}` });
	}
	return out;
}
