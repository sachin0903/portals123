import type { FormField, RepeatingColumn } from './types.js';

/** Columns authored on a repeating field. Objects that are not columns are ignored, so a
    legacy options shape stored on the same field does not become a phantom column. */
export function repeatingColumns(options: FormField['options']): RepeatingColumn[] {
	if (!Array.isArray(options)) return [];
	const columns: RepeatingColumn[] = [];
	for (const item of options) {
		if (!item || typeof item !== 'object' || !('key' in item)) continue;
		const record = item as unknown as Record<string, unknown>;
		const key = typeof record.key === 'string' ? record.key.trim() : '';
		if (!key) continue;
		const label =
			typeof record.label === 'string' && record.label.trim() ? record.label.trim() : key;
		const type = typeof record.type === 'string' && record.type ? record.type : 'text';
		const column: RepeatingColumn = {
			key,
			label,
			type,
			required: record.required !== false
		};
		if (record.options !== undefined) column.options = record.options as RepeatingColumn['options'];
		columns.push(column);
	}
	return columns;
}

/** Same blank rule as the server: `0` and `false` are answers; `''`, `[]` and `null` are not. */
export function isBlankCell(value: unknown): boolean {
	if (value === undefined || value === null) return true;
	if (typeof value === 'string' && value.trim() === '') return true;
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === 'object') return Object.keys(value as object).length === 0;
	return false;
}

/** Mirrors `app/flows/form_validation.py::repeating_gap`. A required group needs a row. A
    required column (the default) left blank on any row is rejected, including on an optional
    group once a row has been started. */
export function validateRepeating(
	field: Pick<FormField, 'label' | 'required' | 'options'>,
	value: unknown
): string | null {
	const columns = repeatingColumns(field.options ?? null);
	const rows = Array.isArray(value) ? value : null;
	if (!rows || rows.length === 0) {
		return field.required ? `${field.label} needs at least one row` : null;
	}
	for (const row of rows) {
		if (!row || typeof row !== 'object' || Array.isArray(row)) {
			const blank = columns.find((column) => column.required !== false);
			return blank ? `${blank.label} is required` : `${field.label} has an incomplete row`;
		}
		const record = row as Record<string, unknown>;
		for (const column of columns) {
			if (column.required === false) continue;
			if (isBlankCell(record[column.key])) return `${column.label} is required`;
		}
	}
	return null;
}
