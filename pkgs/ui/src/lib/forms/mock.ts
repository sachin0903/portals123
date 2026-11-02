/**
 * Seed form data + read helpers, shared by the platform (adviser) and client
 * portals. Shapes mirror the planned document-management/forms API so wiring
 * real data later is a swap, not a rewrite (docs/forms.md §9). Mutation/response
 * helpers stay app-local (they operate on these same arrays by reference).
 */
import type { Form, FormField } from './types.js';

export const forms: Form[] = [
	{
		uid: 'frm-risk-questionnaire',
		code: 'risk_questionnaire',
		name: 'Attitude to Risk Questionnaire',
		description: 'Assesses a client’s attitude to, and capacity for, investment risk.',
		status: 'published',
		entityType: 'applicant',
		fieldCount: 6,
		createdAt: '2026-05-14T09:30:00Z',
		createdBy: 'Priya Nair',
		// Ties to the seeded (disabled) dataset `ds-risk-questionnaire`.
		questionnaireDatasetCode: 'risk_questionnaire_output'
	},
	{
		uid: 'frm-fact-find',
		code: 'client_fact_find',
		name: 'Client Fact Find',
		description: 'Core client data capture — personal, contact and financial details.',
		status: 'draft',
		entityType: 'applicant',
		documentType: 'factfind',
		fieldCount: 12,
		createdAt: '2026-06-02T14:05:00Z',
		createdBy: 'James Okafor'
	}
];

/** A form's fields, keyed by form uid (the 1:N side-table). */
export const fieldsByForm: Record<string, FormField[]> = {
	'frm-risk-questionnaire': [
		{
			uid: 'fld-risk-horizon',
			fieldKey: 'investment_horizon',
			fieldType: 'select',
			label: 'Investment time horizon',
			section: 'Time horizon',
			description: 'How long before you expect to draw on these investments?',
			required: true,
			conditions: null,
			validation: null,
			options: [
				{ value: 'short', label: 'Less than 5 years' },
				{ value: 'medium', label: '5 to 10 years' },
				{ value: 'long', label: 'More than 10 years' }
			],
			sortOrder: 1
		},
		{
			uid: 'fld-risk-tolerance',
			fieldKey: 'risk_tolerance',
			fieldType: 'select',
			label: 'Attitude to risk',
			section: 'Attitude',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: [
				{ value: 'cautious', label: 'Cautious' },
				{ value: 'balanced', label: 'Balanced' },
				{ value: 'adventurous', label: 'Adventurous' }
			],
			sortOrder: 2
		},
		{
			uid: 'fld-risk-loss-reaction',
			fieldKey: 'loss_reaction',
			fieldType: 'select',
			label: 'If your portfolio fell 20% in a year, you would most likely…',
			section: 'Attitude',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: [
				{ value: 'sell', label: 'Sell to avoid further losses' },
				{ value: 'hold', label: 'Hold and wait for recovery' },
				{ value: 'buy', label: 'Invest more at lower prices' }
			],
			sortOrder: 3
		},
		{
			uid: 'fld-risk-experience',
			fieldKey: 'experience_products',
			fieldType: 'multiSelect',
			label: 'Products you have invested in before',
			section: 'Experience',
			description: 'Select all that apply.',
			required: false,
			conditions: null,
			validation: null,
			options: [
				{ value: 'equities', label: 'Shares / equities' },
				{ value: 'bonds', label: 'Bonds / gilts' },
				{ value: 'funds', label: 'Funds' },
				{ value: 'property', label: 'Property' },
				{ value: 'crypto', label: 'Cryptoassets' }
			],
			sortOrder: 4
		},
		{
			uid: 'fld-risk-crypto-years',
			fieldKey: 'crypto_experience_years',
			fieldType: 'number',
			label: 'Years of cryptoasset experience',
			section: 'Experience',
			description: null,
			required: false,
			// Only shown when "Cryptoassets" is selected above.
			conditions: {
				conditional: { field: 'experience_products', operator: 'contains', value: 'crypto' }
			},
			validation: { min: 0, max: 60 },
			options: null,
			sortOrder: 5
		},
		{
			uid: 'fld-risk-capacity',
			fieldKey: 'capacity_for_loss',
			fieldType: 'percentage',
			label: 'Proportion of these savings you could afford to lose',
			section: 'Capacity for loss',
			description: null,
			required: true,
			conditions: null,
			validation: { placeholder: '0' },
			options: null,
			sortOrder: 6
		}
	],
	'frm-fact-find': [
		{
			uid: 'fld-ff-name',
			fieldKey: 'client.full_name',
			fieldType: 'text',
			label: 'Full name',
			section: 'Personal details',
			description: null,
			required: true,
			conditions: null,
			validation: { minLength: 2, maxLength: 120 },
			options: null,
			sortOrder: 1
		},
		{
			uid: 'fld-ff-dob',
			fieldKey: 'client.dob',
			fieldType: 'dateOfBirth',
			label: 'Date of birth',
			section: 'Personal details',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 2
		},
		{
			uid: 'fld-ff-nationality',
			fieldKey: 'client.nationality',
			fieldType: 'combobox',
			label: 'Nationality',
			section: 'Personal details',
			description: null,
			required: false,
			conditions: null,
			validation: null,
			// Options resolved at render time from the `nationalities` data source.
			options: { source: 'nationalities' },
			sortOrder: 3
		},
		{
			uid: 'fld-ff-ni',
			fieldKey: 'client.ni_number',
			fieldType: 'text',
			label: 'National Insurance number',
			section: 'Personal details',
			description: null,
			required: false,
			conditions: null,
			validation: { placeholder: 'AB 12 34 56 C' },
			options: null,
			sortOrder: 4
		},
		{
			uid: 'fld-ff-email',
			fieldKey: 'client.email',
			fieldType: 'email',
			label: 'Email address',
			section: 'Contact',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 5
		},
		{
			uid: 'fld-ff-phone',
			fieldKey: 'client.phone',
			fieldType: 'phoneNumber',
			label: 'Mobile number',
			section: 'Contact',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 6
		},
		{
			uid: 'fld-ff-marketing',
			fieldKey: 'marketing_opt_in',
			fieldType: 'checkbox',
			label: 'Happy to receive occasional updates by email',
			section: 'Contact',
			description: null,
			required: false,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 7
		},
		{
			uid: 'fld-ff-employment',
			fieldKey: 'employment_status',
			fieldType: 'select',
			label: 'Employment status',
			section: 'Financial',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: [
				{ value: 'employed', label: 'Employed' },
				{ value: 'self_employed', label: 'Self-employed' },
				{ value: 'retired', label: 'Retired' },
				{ value: 'other', label: 'Other' }
			],
			sortOrder: 8
		},
		{
			uid: 'fld-ff-employer',
			fieldKey: 'employer_name',
			fieldType: 'text',
			label: 'Employer name',
			section: 'Financial',
			description: null,
			required: false,
			// Only shown for employed clients.
			conditions: {
				conditional: { field: 'employment_status', operator: 'equals', value: 'employed' }
			},
			validation: null,
			options: null,
			sortOrder: 9
		},
		{
			uid: 'fld-ff-income',
			fieldKey: 'annual_income',
			fieldType: 'currency',
			label: 'Gross annual income',
			section: 'Financial',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: { currency: 'GBP' },
			sortOrder: 10
		},
		{
			uid: 'fld-ff-sortcode',
			fieldKey: 'bank.sort_code',
			fieldType: 'sortCode',
			label: 'Bank sort code',
			section: 'Financial',
			description: null,
			required: false,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 11
		},
		{
			uid: 'fld-ff-notes',
			fieldKey: 'adviser_notes',
			fieldType: 'textarea',
			label: 'Adviser notes',
			section: 'Notes',
			description: 'Any additional context captured during the meeting.',
			required: false,
			conditions: null,
			validation: { minLength: 0, maxLength: 1000, rows: 4 },
			options: null,
			sortOrder: 12
		}
	]
};

export function getForm(uid: string): Form | undefined {
	return forms.find((f) => f.uid === uid);
}

export function getFormFields(uid: string): FormField[] {
	return (fieldsByForm[uid] ?? []).slice().sort((a, b) => a.sortOrder - b.sortOrder);
}

export function getFormByCode(code: string | null | undefined): Form | undefined {
	return code ? forms.find((f) => f.code === code) : undefined;
}
