import type { FormField } from '../types.js';
import { nationalityOptions } from '../../national-identifier.js';
import { closedFieldOptions, withStoredChoice } from '../vocabularies.js';

// Lifted verbatim from platform-1 `pkgs/fields/src/runtime/options.ts`.

export type ResolvedOption = { value: string; label: string };

type DataSourceRef = { source: string; valueKey?: string; labelKey?: string };

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getOptionsArray(raw: FormField['options']): unknown[] {
	if (!raw) return [];
	if (Array.isArray(raw)) return raw;
	if (isRecord(raw) && 'options' in raw && Array.isArray((raw as { options?: unknown }).options)) {
		return (raw as { options: unknown[] }).options;
	}
	return [];
}

function getSourceRef(raw: FormField['options']): DataSourceRef | null {
	if (!raw) return null;
	if (!isRecord(raw)) return null;
	if (!('source' in raw)) return null;
	if (
		typeof (raw as { source?: unknown }).source !== 'string' ||
		String((raw as { source?: unknown }).source).trim() === ''
	)
		return null;
	return {
		source: String((raw as { source: string }).source),
		valueKey:
			typeof (raw as { valueKey?: unknown }).valueKey === 'string'
				? (raw as { valueKey: string }).valueKey
				: undefined,
		labelKey:
			typeof (raw as { labelKey?: unknown }).labelKey === 'string'
				? (raw as { labelKey: string }).labelKey
				: undefined
	};
}

/**
 * Resolves options from a field definition.
 *
 * Supported shapes (legacy-compatible):
 * - Array of objects: [{ value, label }]
 * - Array of strings: ["foo", "bar"]
 * - Object with nested array: { options: [...] }
 * - Object source reference: { source: "nationalities", valueKey?, labelKey? }
 */
export function resolveOptions(
	field: Pick<FormField, 'options'>,
	dataSources: Record<string, unknown[]> = {}
): ResolvedOption[] {
	const raw = field.options;
	const optionsArray = getOptionsArray(raw);

	if (Array.isArray(optionsArray) && optionsArray.length > 0) {
		return optionsArray
			.map((opt) => {
				if (typeof opt === 'string') return { value: opt, label: opt };
				if (isRecord(opt) && 'value' in opt) {
					const v = (opt as { value?: unknown }).value;
					if (v === undefined || v === null) return null;
					const label = 'label' in opt ? (opt as { label?: unknown }).label : undefined;
					return { value: String(v), label: String(label ?? v) };
				}
				if (isRecord(opt) && 'code' in opt) {
					const code = (opt as { code?: unknown }).code;
					if (code === undefined || code === null) return null;
					const name = (opt as { name?: unknown }).name;
					const adjective = (opt as { adjective?: unknown }).adjective;
					return { value: String(code), label: String(name ?? adjective ?? code) };
				}
				return null;
			})
			.filter(Boolean) as ResolvedOption[];
	}

	const sourceRef = getSourceRef(raw);
	if (sourceRef && Array.isArray(dataSources[sourceRef.source])) {
		const items = dataSources[sourceRef.source] as unknown[];
		const valueKey = sourceRef.valueKey || 'id';
		const labelKey = sourceRef.labelKey || 'name';

		// Nationalities are labelled by ADJECTIVE, disambiguated with the country in brackets where
		// two share one. `nationalityOptions` is that rule, and it is SHARED with the client record
		// — it was written out twice, and the copies disagreed about a country with a blank
		// adjective: this branch fell back to the name, the record's copy dropped the row. Since
		// the identifier REGIME is derived from the nationality, an unpickable country is a
		// reporting gap rather than a cosmetic one. Source order is preserved either way.
		if (sourceRef.source === 'nationalities') {
			return nationalityOptions(
				items.filter(isRecord).map((item) => ({
					code: String(item[valueKey] ?? item.code ?? ''),
					name: item.name == null ? null : String(item.name),
					adjective: item.adjective == null ? null : String(item.adjective)
				}))
			);
		}

		return items
			.map((item) => {
				if (!isRecord(item)) return null;
				const rawValue = item[valueKey];
				// Filter out undefined/null/empty values.
				if (rawValue === undefined || rawValue === null || rawValue === '') return null;
				const value = String(rawValue);
				const label = String(item[labelKey] ?? rawValue);
				return { value, label };
			})
			.filter(Boolean) as ResolvedOption[];
	}

	return [];
}

/**
 * Options a field actually offers. A closed type (`title`, `gender`, `marital`,
 * `employment`) ignores anything authored on the field and uses the shared vocabulary.
 * A value already stored outside that list stays selectable, so a synced title is not blanked.
 */
export function fieldChoices(
	field: Pick<FormField, 'fieldType' | 'options'>,
	dataSources: Record<string, unknown[]> = {},
	current?: unknown
): ResolvedOption[] {
	const closed = closedFieldOptions(field.fieldType);
	if (!closed) return resolveOptions(field, dataSources);
	return withStoredChoice(closed, current, { digitsAreCodes: field.fieldType === 'title' });
}
