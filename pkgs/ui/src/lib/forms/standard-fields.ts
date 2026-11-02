// ─────────────────────────────────────────────────────────────────────────
// Standard field library — predefined variables for common financial document
// types. Ported from platform-1's `lib/schemas/standard-fields.ts`. In the form
// builder these seed a field-key suggestion picker (choose `client.full_name`
// and prefill the label/type) so authors need not memorise variable names — and
// aligning a form's answer keys with the template variable names is what keeps
// the future questionnaire→template connection clean (docs/forms.md §4, §9).
//
// key     = the Jinja2 variable name used in a template ({{ client.full_name }})
// example = the fixture/sample value shown in previews
// ─────────────────────────────────────────────────────────────────────────

export interface StandardField {
	key: string;
	label: string;
	type: 'string' | 'number' | 'integer';
	example: string | number;
	description?: string;
}

export interface FieldCategory {
	id: string;
	label: string;
	fields: StandardField[];
}

export const STANDARD_FIELD_CATEGORIES: FieldCategory[] = [
	{
		id: 'client',
		label: 'Client',
		fields: [
			{ key: 'client.full_name', label: 'Full Name', type: 'string', example: 'Jane Smith' },
			{ key: 'client.first_name', label: 'First Name', type: 'string', example: 'Jane' },
			{ key: 'client.surname', label: 'Surname', type: 'string', example: 'Smith' },
			{ key: 'client.dob', label: 'Date of Birth', type: 'string', example: '15 March 1968' },
			{ key: 'client.email', label: 'Email', type: 'string', example: 'jane.smith@example.com' },
			{
				key: 'client.address',
				label: 'Address',
				type: 'string',
				example: '14 Elm Street, London, SW1A 1AA'
			},
			{ key: 'client.ni_number', label: 'NI Number', type: 'string', example: 'AB 12 34 56 C' }
		]
	},
	{
		id: 'dates',
		label: 'Dates',
		fields: [
			{ key: 'date', label: 'Report Date', type: 'string', example: '1 June 2026' },
			{
				key: 'illustration_date',
				label: 'Illustration Date',
				type: 'string',
				example: 'June 2026'
			},
			{ key: 'valuation_date', label: 'Valuation Date', type: 'string', example: '1 June 2026' },
			{ key: 'tax_year', label: 'Tax Year', type: 'string', example: '2025/26' },
			{ key: 'review_date', label: 'Review Date', type: 'string', example: '1 June 2027' }
		]
	},
	{
		id: 'adviser',
		label: 'Adviser',
		fields: [
			{ key: 'adviser.name', label: 'Adviser Name', type: 'string', example: 'James Thornton' },
			{
				key: 'adviser.firm',
				label: 'Firm Name',
				type: 'string',
				example: 'Thornton Wealth Management'
			},
			{
				key: 'adviser.email',
				label: 'Adviser Email',
				type: 'string',
				example: 'james.thornton@example.com'
			},
			{ key: 'adviser.telephone', label: 'Telephone', type: 'string', example: '020 7946 0123' }
		]
	},
	{
		id: 'financial',
		label: 'Financial',
		fields: [
			{ key: 'current_value', label: 'Current Value', type: 'number', example: 85000 },
			{ key: 'total_value', label: 'Total Value', type: 'number', example: 85000 },
			{ key: 'opening_value', label: 'Opening Value', type: 'number', example: 72000 },
			{
				key: 'annual_contribution',
				label: 'Annual Contribution',
				type: 'number',
				example: 12000
			},
			{
				key: 'annual_charge',
				label: 'Annual Charge (%)',
				type: 'number',
				example: 0.0075,
				description: 'As a decimal, e.g. 0.0075 for 0.75%'
			},
			{ key: 'retirement_age', label: 'Retirement Age', type: 'integer', example: 65 },
			{ key: 'model_name', label: 'Investment Model', type: 'string', example: 'Balanced Growth' },
			{ key: 'portfolio_value', label: 'Portfolio Value', type: 'number', example: 250000 }
		]
	},
	{
		id: 'rates',
		label: 'Growth Rates',
		fields: [
			{
				key: 'cautious_rate',
				label: 'Cautious Rate',
				type: 'number',
				example: 0.02,
				description: 'Decimal, e.g. 0.02 for 2%'
			},
			{ key: 'intermediate_rate', label: 'Intermediate Rate', type: 'number', example: 0.05 },
			{ key: 'optimistic_rate', label: 'Optimistic Rate', type: 'number', example: 0.08 }
		]
	},
	{
		id: 'document',
		label: 'Document',
		fields: [
			{
				key: 'title',
				label: 'Document Title',
				type: 'string',
				example: 'SIPP Investment Illustration'
			},
			{ key: 'subtitle', label: 'Subtitle', type: 'string', example: 'Annual Review' },
			{ key: 'reference', label: 'Reference Number', type: 'string', example: 'REF-2026-001' },
			{
				key: 'footer_note',
				label: 'Footer Note',
				type: 'string',
				example: 'This document is for illustration purposes only.'
			}
		]
	}
];

export const ALL_STANDARD_FIELDS: StandardField[] = STANDARD_FIELD_CATEGORIES.flatMap(
	(c) => c.fields
);

/** Build a fixture map from a list of field keys. */
export function buildFixturesFromKeys(keys: string[]): Record<string, unknown> {
	const fixtures: Record<string, unknown> = {};
	for (const key of keys) {
		const field = ALL_STANDARD_FIELDS.find((f) => f.key === key);
		if (field) {
			// Expand dotted keys: client.full_name → { client: { full_name: ... } }
			const parts = key.split('.');
			let obj = fixtures as Record<string, unknown>;
			for (let i = 0; i < parts.length - 1; i++) {
				const seg = parts[i];
				if (seg === undefined) continue;
				if (!obj[seg] || typeof obj[seg] !== 'object') obj[seg] = {};
				obj = obj[seg] as Record<string, unknown>;
			}
			const last = parts[parts.length - 1];
			if (last !== undefined) obj[last] = field.example;
		}
	}
	return fixtures;
}

/** Build a JSON Schema from a list of scalar field keys + collection field names. */
export function buildInputSchema(
	scalarKeys: string[],
	collectionKeys: string[]
): Record<string, unknown> {
	const properties: Record<string, unknown> = {};
	for (const key of scalarKeys) {
		const field = ALL_STANDARD_FIELDS.find((f) => f.key === key);
		properties[key] = {
			type: field?.type ?? 'string',
			description: field?.label ?? key
		};
	}
	for (const key of collectionKeys) {
		properties[key] = { type: 'array', description: key };
	}
	return {
		type: 'object',
		required: [...scalarKeys, ...collectionKeys],
		properties
	};
}
