import type { FormField } from './types.js';

/** Wrappers that keep the word "contribution". Every other product says "payment in". */
const CONTRIBUTION_PRODUCTS = new Set(['SIPP', 'JISA', 'TPPENSION']);

export function isContributionProduct(productType: unknown): boolean {
	return CONTRIBUTION_PRODUCTS.has(
		String(productType ?? '')
			.trim()
			.toUpperCase()
	);
}

function accountFact(applicationData: Record<string, unknown>, key: string): unknown {
	const account = applicationData.account;
	if (!account || typeof account !== 'object') return undefined;
	return (account as Record<string, unknown>)[key];
}

function withoutPayAway(field: FormField): FormField {
	const options = field.options;
	if (
		!options ||
		typeof options !== 'object' ||
		Array.isArray(options) ||
		!('options' in options)
	) {
		return field;
	}
	const list = options.options;
	if (!Array.isArray(list)) return field;
	return {
		...field,
		options: {
			...options,
			options: list.filter((option) => {
				if (!option || typeof option !== 'object') return true;
				return (option as { value?: unknown }).value !== 'pay_away';
			})
		}
	};
}

/** Product-specific label, and dividend options a custodian is actually offered.

 * Pay away stays on the field so an Epworth account can still choose it. Every other custodian
 * sees Reinvest or Cash. An answer that is already pay away stays visible so it is not cleared.
 */
export function presentField(
	field: FormField,
	applicationData: Record<string, unknown> = {},
	values: Record<string, unknown> = {}
): FormField {
	let next = field;
	const alt = field.validation?.contributionLabel;
	if (
		typeof alt === 'string' &&
		isContributionProduct(accountFact(applicationData, 'product_type'))
	) {
		next = { ...next, label: alt };
	}
	if (field.fieldKey === 'dividend_treatment') {
		const custodian = String(accountFact(applicationData, 'custodian') ?? '')
			.trim()
			.toLowerCase();
		if (custodian !== 'epworth' && values[field.fieldKey] !== 'pay_away') {
			next = withoutPayAway(next);
		}
	}
	return next;
}

export type FormRow =
	{ kind: 'grid'; fields: FormField[] } | { kind: 'pair'; option: FormField; details: FormField[] };

/** Group a left-column option with the right-column fields that follow it.

 * Hidden fields are already dropped before this runs, so an unticked option is a row on its own.
 */
export function layoutFields(fields: FormField[]): FormRow[] {
	const rows: FormRow[] = [];
	let grid: FormField[] = [];
	const flush = () => {
		if (grid.length === 0) return;
		rows.push({ kind: 'grid', fields: grid });
		grid = [];
	};
	let index = 0;
	while (index < fields.length) {
		const field = fields[index]!;
		if (field.validation?.column !== 'left') {
			grid.push(field);
			index += 1;
			continue;
		}
		flush();
		const details: FormField[] = [];
		index += 1;
		while (index < fields.length && fields[index]!.validation?.column === 'right') {
			details.push(fields[index]!);
			index += 1;
		}
		rows.push({ kind: 'pair', option: field, details });
	}
	flush();
	return rows;
}
