import type { FormField } from '../types.js';

import { formatDate } from '../../dates.js';
import { fieldChoices, resolveOptions } from './options';
import { closedFieldOptions } from '../vocabularies';
import { getNestedValue } from './visibility';

// Lifted from platform-1 `pkgs/fields/src/runtime/format.ts` (numeric `id`
// dropped from the display helper — the portal keys on `fieldKey`/`uid`).

export type FormatFieldValueInput = {
	value: unknown;
	field: Pick<FormField, 'fieldType' | 'options'>;
	dataSources?: Record<string, unknown[]>;
};

export function formatFieldValue({ value, field, dataSources }: FormatFieldValueInput): string {
	if (value === null || value === undefined || value === '') return '';

	if (Array.isArray(value)) {
		// Prefer labels for multi-value fields when we have options.
		if (field.fieldType === 'multiSelect' || field.fieldType === 'multiPicker') {
			const options = resolveOptions(field, dataSources ?? {});
			return value
				.map((v) => {
					const match = options.find((o) => o.value === String(v));
					return match?.label ?? String(v);
				})
				.join(', ');
		}
		return value.map((v) => String(v)).join(', ');
	}

	if (field.fieldType === 'checkbox' || typeof value === 'boolean') {
		return value === true ? 'Yes' : 'No';
	}

	if (field.fieldType === 'date' || field.fieldType === 'dateOfBirth') {
		// An unparseable value is shown as typed rather than blanked.
		return formatDate(String(value), String(value));
	}

	if (
		field.fieldType === 'select' ||
		field.fieldType === 'combobox' ||
		closedFieldOptions(field.fieldType)
	) {
		const options = fieldChoices(field, dataSources ?? {}, value);
		const match = options.find((o) => o.value === String(value));
		return match?.label ?? String(value);
	}

	if (field.fieldType === 'percentage') {
		return `${String(value)}%`;
	}

	if (field.fieldType === 'currency' || field.fieldType === 'money') {
		const currency =
			field.options &&
			typeof field.options === 'object' &&
			!Array.isArray(field.options) &&
			'currency' in (field.options as Record<string, unknown>)
				? String((field.options as Record<string, unknown>).currency || 'GBP')
				: 'GBP';
		const num = typeof value === 'number' ? value : Number(String(value).replace(/,/g, ''));
		if (!Number.isFinite(num)) return String(value);
		try {
			return new Intl.NumberFormat('en-GB', { style: 'currency', currency }).format(num);
		} catch {
			return String(value);
		}
	}

	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

export type FormatDynamicFieldsForDisplayInput = {
	fields: Array<
		Pick<
			FormField,
			| 'label'
			| 'fieldKey'
			| 'fieldType'
			| 'section'
			| 'sortOrder'
			| 'conditions'
			| 'validation'
			| 'options'
		>
	>;
	data: Record<string, unknown>;
	applicationData?: Record<string, unknown>;
	dataSources?: Record<string, unknown[]>;
};

/**
 * Convenience helper for display components: filters to visible+non-empty
 * fields, sorts, and formats values.
 */
export function formatDynamicFieldsForDisplay({
	fields,
	data,
	applicationData,
	dataSources
}: FormatDynamicFieldsForDisplayInput) {
	return fields
		.map((f) => {
			const raw = getNestedValue(data, f.fieldKey, applicationData);
			const formatted = formatFieldValue({ value: raw, field: f, dataSources });
			return { field: f, raw, formatted };
		})
		.filter((x) => x.formatted !== '');
}
