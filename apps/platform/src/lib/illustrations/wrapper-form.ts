// The GIA / ISA illustration form, on the same DynamicForm the pension illustration uses.
//
// The questions differ — a term instead of a retirement age, a platform fee when the firm has more
// than one, a joint owner on a GIA — but the field types, the two-per-row plan, the optional
// sections and the payload shaping are the same idea as `$lib/illustrations/form`. Identity stays
// on the client record.

import type { FieldOptions, FieldValidation, FormField } from '@graphene/ui';

export type WrapperKind = 'GIA' | 'ISA';

export type PlatformFeeChoice = {
	id: number;
	name: string;
	summary: string;
};

const FREQUENCIES = [
	{ value: 'monthly', label: 'Monthly' },
	{ value: 'quarterly', label: 'Quarterly' },
	{ value: 'annual', label: 'Annually' }
];

interface FieldSpec {
	section: string;
	key: string;
	type: string;
	label: string;
	options?: FieldOptions;
	validation?: FieldValidation;
	required?: boolean;
}

const field = (spec: FieldSpec): Omit<FormField, 'sortOrder'> => ({
	uid: `wrap-${spec.key}`,
	fieldKey: spec.key,
	fieldType: spec.type,
	label: spec.label,
	section: spec.section,
	description: null,
	required: spec.required ?? false,
	conditions: null,
	validation: spec.validation ?? null,
	options: spec.options ?? null
});

export const WRAPPER_SECTIONS: Record<
	string,
	{ description?: string; optional?: boolean; defaultOn?: boolean }
> = {
	Plan: {
		description:
			'What is being projected, and on what. Growth is the model’s target return less 2 percentage points.'
	},
	'Adviser charges': {
		description:
			'Your fees, so the projection is net of them. Leave both blank to show the projection before adviser fees.'
	},
	'Single contribution': {
		description: 'One payment into the plan.',
		optional: true,
		defaultOn: false
	},
	'Regular contribution': {
		description: 'Money paid in on a schedule, on top of any single payment.',
		optional: true,
		defaultOn: false
	},
	Withdrawals: {
		description: 'Money taken out during the projection.',
		optional: true,
		defaultOn: false
	},
	'Joint owner': {
		description: 'A second person named on a GIA.',
		optional: true,
		defaultOn: false
	}
};

/** Fields for one wrapper. A platform-fee question appears only when the firm has more than one. */
export function wrapperIllustrationFields(
	wrapper: WrapperKind,
	chooseFee: boolean,
	today = new Date().toISOString().slice(0, 10)
): FormField[] {
	const specs: FieldSpec[] = [
		{
			section: 'Plan',
			key: 'existing_balance',
			type: 'currency',
			label: 'Existing balance',
			validation: { width: 6, placeholder: '0.00' }
		},
		{
			section: 'Plan',
			key: 'other_platform_assets',
			type: 'currency',
			label: 'Other platform assets',
			validation: { width: 6, placeholder: '0.00' }
		},
		{
			section: 'Plan',
			key: 'model_id',
			type: 'combobox',
			label: 'Model',
			options: { source: 'models', valueKey: 'id', labelKey: 'name' },
			validation: { width: 6 },
			required: true
		},
		...(chooseFee
			? [
					{
						section: 'Plan',
						key: 'fee_package_id',
						type: 'select',
						label: 'Platform fee',
						options: { source: 'fees', valueKey: 'id', labelKey: 'name' },
						validation: { width: 6 },
						required: true
					} satisfies FieldSpec
				]
			: []),
		{
			section: 'Plan',
			key: 'start_date',
			type: 'date',
			label: 'Start date',
			validation: { width: 6, defaultValue: today },
			required: true
		},
		{
			section: 'Plan',
			key: 'term_years',
			type: 'number',
			label: 'Term (years)',
			validation: { width: 6, min: 1, max: 40, defaultValue: 10 },
			required: true
		},
		{
			section: 'Adviser charges',
			key: 'adviser_ongoing',
			type: 'percentage',
			label: 'Ongoing (% pa)',
			validation: { width: 6, placeholder: '0.75' }
		},
		{
			section: 'Adviser charges',
			key: 'adviser_initial_amount',
			type: 'currency',
			label: 'Initial',
			validation: { width: 6 }
		},
		{
			section: 'Single contribution',
			key: 'single_amount',
			type: 'currency',
			label: 'Amount',
			validation: { width: 6, placeholder: '0.00' },
			required: true
		},
		{
			section: 'Single contribution',
			key: 'single_on',
			type: 'date',
			label: 'Date',
			validation: { width: 6, defaultValue: today },
			required: true
		},
		{
			section: 'Regular contribution',
			key: 'regular_amount',
			type: 'currency',
			label: 'Amount',
			validation: { width: 6, placeholder: '0.00' },
			required: true
		},
		{
			section: 'Regular contribution',
			key: 'regular_frequency',
			type: 'select',
			label: 'Frequency',
			options: FREQUENCIES,
			validation: { width: 6, defaultValue: 'monthly' },
			required: true
		},
		{
			section: 'Regular contribution',
			key: 'regular_start',
			type: 'date',
			label: 'Start',
			validation: { width: 6, defaultValue: today },
			required: true
		},
		{
			section: 'Regular contribution',
			key: 'regular_end',
			type: 'date',
			label: 'End',
			validation: { width: 6 }
		},
		{
			section: 'Regular contribution',
			key: 'escalation',
			type: 'percentage',
			label: 'Escalation (% pa)',
			validation: { width: 6, placeholder: '0' }
		},
		{
			section: 'Withdrawals',
			key: 'withdrawal_amount',
			type: 'currency',
			label: 'Amount',
			validation: { width: 6, placeholder: '0.00' },
			required: true
		},
		{
			section: 'Withdrawals',
			key: 'withdrawal_frequency',
			type: 'select',
			label: 'Frequency',
			options: FREQUENCIES,
			validation: { width: 6, defaultValue: 'monthly' },
			required: true
		},
		{
			section: 'Withdrawals',
			key: 'withdrawal_start',
			type: 'date',
			label: 'Start',
			validation: { width: 6, defaultValue: today },
			required: true
		},
		{
			section: 'Withdrawals',
			key: 'withdrawal_end',
			type: 'date',
			label: 'End',
			validation: { width: 6 }
		}
	];
	if (wrapper === 'GIA') {
		specs.push(
			{
				section: 'Joint owner',
				key: 'joint_owner_name',
				type: 'text',
				label: 'Name',
				validation: { width: 6 },
				required: true
			},
			{
				section: 'Joint owner',
				key: 'joint_owner_dob',
				type: 'date',
				label: 'Date of birth',
				validation: { width: 6 },
				required: true
			}
		);
	}
	return specs.map((spec, i) => ({ ...field(spec), sortOrder: i }));
}

const num = (v: unknown): number | null => {
	const s = String(v ?? '').trim();
	return s === '' ? null : Number(s);
};

/** A typed percent (0.75) as the fraction the engine stores (0.0075). */
const fraction = (v: unknown): string | null => {
	const n = num(v);
	if (n === null) return null;
	return (n / 100).toFixed(6);
};

const money = (v: unknown): string | null => {
	const n = num(v);
	return n === null ? null : String(n);
};

/**
 * Shape collected DynamicForm values into `POST …/illustrations/investment`.
 *
 * `collect()` has already dropped switched-off sections, so a missing contribution key means that
 * section was off. Percent fields are adviser-facing percentage points.
 */
export function toWrapperIllustrationPayload(
	values: Record<string, unknown>,
	wrapper: WrapperKind,
	soleFeeId: number | null,
	today = new Date().toISOString().slice(0, 10)
): Record<string, unknown> {
	const contributions = [];
	const single = money(values.single_amount);
	if (single) {
		contributions.push({
			type: 'single',
			amount: single,
			on: String(values.single_on ?? today)
		});
	}
	const regular = money(values.regular_amount);
	if (regular) {
		contributions.push({
			type: 'regular',
			amount: regular,
			start: String(values.regular_start ?? today),
			end: values.regular_end ? String(values.regular_end) : null,
			frequency: values.regular_frequency ?? 'monthly',
			escalation: fraction(values.escalation) ?? '0'
		});
	}
	const withdrawals = [];
	const withdrawal = money(values.withdrawal_amount);
	if (withdrawal) {
		withdrawals.push({
			type: 'regular',
			amount: withdrawal,
			start: String(values.withdrawal_start ?? today),
			end: values.withdrawal_end ? String(values.withdrawal_end) : null,
			frequency: values.withdrawal_frequency ?? 'monthly',
			escalation: '0'
		});
	}
	const chosenFee = num(values.fee_package_id);
	return {
		wrapper,
		start_date: String(values.start_date ?? today),
		term_years: num(values.term_years) ?? 10,
		model_id: num(values.model_id),
		fee_package_id: chosenFee ?? soleFeeId,
		existing_balance: money(values.existing_balance) ?? '0',
		other_platform_assets: money(values.other_platform_assets) ?? '0',
		adviser_ongoing: fraction(values.adviser_ongoing) ?? '0',
		adviser_initial_amount: money(values.adviser_initial_amount),
		adviser_initial_rate: null,
		joint_owner_name:
			wrapper === 'GIA' && values.joint_owner_name
				? String(values.joint_owner_name).trim() || null
				: null,
		joint_owner_dob:
			wrapper === 'GIA' && values.joint_owner_dob ? String(values.joint_owner_dob) : null,
		contributions,
		withdrawals
	};
}
