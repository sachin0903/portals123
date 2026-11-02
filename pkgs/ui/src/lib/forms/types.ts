import type { StatusTone } from '../components/ui/status-badge/index.js';
import type { DocumentType } from '../templates/types.js';

// ─────────────────────────────────────────────────────────────────────────
// Forms / dynamic-fields domain types — a staff-authored, typed, conditional
// data-collection form (a "questionnaire"/fact-find). Ported from platform-1's
// `@repo/fields` model and rebuilt for the portal stack.
//
// Like the Template/Dataset types, these are the shared SHAPES; app-specific
// data (the form list, the field store, responses) lives in each app's
// `$lib/demo`. They are drawn to mirror an eventual `documents.*`-style API
// (public `uid` + human `code`, string timestamps, no numeric DB ids on the
// wire) so wiring a real backend later is a swap, not a rewrite.
// ─────────────────────────────────────────────────────────────────────────

export type FormStatus = 'draft' | 'published' | 'archived';

export const FORM_STATUS_META: Record<FormStatus, { label: string; tone: StatusTone }> = {
	draft: { label: 'Draft', tone: 'neutral' },
	published: { label: 'Published', tone: 'success' },
	archived: { label: 'Archived', tone: 'neutral' }
};

/** Carried from platform-1; drives the condition-editor's model picker. */
export type FormEntityType = 'applicant' | 'account';

// The field types the builder offers, in platform-1's order — plus `display`, a read-only value
// pulled from the record context (not an input). The renderer additionally tolerates legacy aliases
// present in imported data but never offered here (multiPicker≡multiSelect, money≡currency,
// accountNumber≡bankAccountNumber, phone≡phoneNumber).
// `phoneNumber` and `phoneNumbers` are NOT aliases of each other and the plural is not a typo: the
// singular captures ONE number as a string (the primary mobile), the plural captures the repeatable
// labelled list beside it and answers with `PhoneNumber[]`.
export type FormFieldType =
	| 'text'
	| 'textarea'
	| 'number'
	| 'checkbox'
	| 'date'
	| 'dateOfBirth'
	| 'email'
	| 'phoneNumber'
	| 'bankAccountNumber'
	| 'sortCode'
	| 'currency'
	| 'percentage'
	| 'select'
	| 'segmented'
	| 'combobox'
	| 'multiSelect'
	| 'address'
	| 'beneficiaries'
	| 'phoneNumbers'
	| 'repeating'
	| 'nationalIdentifier'
	// Closed person lists. The options are the shared vocabulary (`vocabularies.ts`),
	// not authored on the field, so these are absent from OPTION_REQUIRED_TYPES.
	| 'title'
	| 'gender'
	| 'marital'
	| 'employment'
	| 'display';

/** One entry of the `phoneNumbers` field type. The book itself stores two scalars (`mobile` and
    `phone`); this shape is for a form that still asks for a labelled list. Exactly two keys, and
    the array's order is the firm's: it is the order they were entered and is never sorted. */
export type PhoneNumber = { label: string; number: string };

/** The labels a number may carry. A closed list so a firm's book stays sortable/filterable by it,
    with `other` as the escape hatch rather than free text. */
export const PHONE_NUMBER_LABELS: { value: string; label: string }[] = [
	{ value: 'home', label: 'Home' },
	{ value: 'work', label: 'Work' },
	{ value: 'landline', label: 'Landline' },
	{ value: 'other', label: 'Other' }
];

export const FORM_FIELD_TYPES: { value: FormFieldType; label: string }[] = [
	{ value: 'text', label: 'Text' },
	{ value: 'textarea', label: 'Text area' },
	{ value: 'number', label: 'Number' },
	{ value: 'checkbox', label: 'Checkbox' },
	{ value: 'date', label: 'Date' },
	{ value: 'dateOfBirth', label: 'Date of birth' },
	{ value: 'email', label: 'Email' },
	{ value: 'phoneNumber', label: 'Phone number' },
	{ value: 'bankAccountNumber', label: 'Bank account number' },
	{ value: 'sortCode', label: 'Sort code' },
	{ value: 'currency', label: 'Currency' },
	{ value: 'percentage', label: 'Percentage' },
	{ value: 'select', label: 'Select' },
	{ value: 'segmented', label: 'Button group' },
	{ value: 'combobox', label: 'Combobox' },
	{ value: 'multiSelect', label: 'Multi-select' },
	{ value: 'address', label: 'Address' },
	{ value: 'beneficiaries', label: 'Beneficiaries' },
	{ value: 'phoneNumbers', label: 'Phone numbers' },
	{ value: 'repeating', label: 'Repeating group' },
	{ value: 'nationalIdentifier', label: 'National identifier' },
	{ value: 'title', label: 'Title' },
	{ value: 'gender', label: 'Gender' },
	{ value: 'marital', label: 'Marital status' },
	{ value: 'employment', label: 'Employment status' },
	{ value: 'display', label: 'Display (read-only)' }
];

/** Scalar types a `repeating` column may use. The group itself, and the bespoke repeaters
    (`beneficiaries`, `phoneNumbers`), are not columns of a group. Composites (`address`,
    `nationalIdentifier`) and read-only `display` are not either — a column is one value. */
const REPEATING_COLUMN_EXCLUDED = new Set<string>([
	'repeating',
	'beneficiaries',
	'phoneNumbers',
	'address',
	'nationalIdentifier',
	'display'
]);
export const REPEATING_COLUMN_TYPES: { value: FormFieldType; label: string }[] =
	FORM_FIELD_TYPES.filter((type) => !REPEATING_COLUMN_EXCLUDED.has(type.value));

// Types that require an options list (gate save validation) and types that
// show the options editor (adds currency, to carry a `{ currency }` code).
// Typed `string[]` so `.includes(field.fieldType)` (an open string) checks cleanly.
export const OPTION_REQUIRED_TYPES: string[] = [
	'combobox',
	'select',
	'segmented',
	'multiSelect',
	'multiPicker'
];
export const OPTION_SHOWN_TYPES: string[] = [...OPTION_REQUIRED_TYPES, 'currency', 'money'];

export type ConditionOperator =
	'equals' | 'notEquals' | 'in' | 'notIn' | 'contains' | 'notContains' | 'isEmpty' | 'isNotEmpty';

export const CONDITION_OPERATORS: {
	value: ConditionOperator;
	label: string;
	takesValue: boolean;
}[] = [
	{ value: 'equals', label: 'Equals', takesValue: true },
	{ value: 'notEquals', label: 'Does not equal', takesValue: true },
	{ value: 'in', label: 'Is one of', takesValue: true },
	{ value: 'notIn', label: 'Is not one of', takesValue: true },
	{ value: 'contains', label: 'Contains', takesValue: true },
	{ value: 'notContains', label: 'Does not contain', takesValue: true },
	{ value: 'isEmpty', label: 'Is empty', takesValue: false },
	{ value: 'isNotEmpty', label: 'Is not empty', takesValue: false }
];

// One-click regex presets for the validation editor's pattern field, so authors
// don't hand-write regular expressions. `pattern` is a JS RegExp source string.
export const PATTERN_PRESETS: { label: string; pattern: string; message: string }[] = [
	{
		label: 'Email',
		pattern: '^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$',
		message: 'Enter a valid email address'
	},
	{ label: 'Year (YYYY)', pattern: '^(19|20)\\d{2}$', message: 'Enter a 4-digit year, e.g. 2026' },
	{
		label: 'Year (any format)',
		pattern: '^(\\d{4}|\\d{2}|\\d{4}[\\/-]\\d{2})$',
		message: 'Enter a year, e.g. 2026, 26, 2026/27 or 2026-27'
	},
	{
		label: 'Tax year (YYYY/YY)',
		pattern: '^\\d{4}\\/\\d{2}$',
		message: 'Enter a tax year, e.g. 2026/27'
	},
	{
		label: 'UK postcode',
		pattern: '^[A-Za-z]{1,2}\\d[A-Za-z\\d]? ?\\d[A-Za-z]{2}$',
		message: 'Enter a valid UK postcode'
	},
	{
		label: 'NI number',
		pattern: '^[A-Za-z]{2} ?\\d{2} ?\\d{2} ?\\d{2} ?[A-Za-z]$',
		message: 'Enter a valid National Insurance number'
	},
	{ label: 'Digits only', pattern: '^\\d+$', message: 'Numbers only' },
	{ label: 'Letters only', pattern: '^[A-Za-z ]+$', message: 'Letters only' },
	{ label: 'URL', pattern: '^https?:\\/\\/.+', message: 'Enter a valid URL' }
];

export interface SimpleCondition {
	field: string;
	operator: ConditionOperator;
	value?: unknown;
}

export interface ConditionGroup {
	logic: 'AND' | 'OR';
	conditions: SimpleCondition[];
}

// Persisted visibility (platform-1 shape): a bare condition, a wrapped
// `{ conditional }` (single or group), or null. The builder writes the wrapped
// form; the runtime reads all three.
export type FieldConditions =
	null | SimpleCondition | { conditional: SimpleCondition | ConditionGroup };

/** Fill a field's value by looking a row up in a `dataSources` list, keyed by ANOTHER field's
    value — a declarative dependent value (e.g. picking an account fills that account's fund/wrapper).
    A targeted lookup, deliberately not a general expression engine. Stored inside `validation`
    (opaque jsonb) so it persists with no backend change. */
export interface ComputedFrom {
	/** `dataSources` key holding the rows to look up. */
	source: string;
	/** the field whose value selects the row. */
	matchOn: string;
	/** the row property compared (as strings) to matchOn's value. Default `'id'`. */
	matchKey?: string;
	/** the row property copied into this field. */
	valueKey: string;
}

// Flat validation bag (platform-1's ValidationJson). `conditional` is a legacy
// visibility fallback only — the builder keeps visibility in `conditions`.
export interface FieldValidation {
	placeholder?: string;
	/** Collect the value but do not render an adviser-editable control. Used for derived sidecars
	    such as national identifier type, whose value is owned by another typed field. */
	hidden?: boolean;
	valueType?: 'string' | 'number' | 'boolean';
	minLength?: number;
	maxLength?: number;
	rows?: number;
	min?: number;
	max?: number;
	pattern?: string;
	patternMessage?: string;
	errorMessage?: string;
	conditional?: SimpleCondition | ConditionGroup;
	/** Layout hint (like `rows`): how much of the row the field occupies, so fields sit side by side
	    and their distribution is adjustable by dragging. Stored as a **column span, 1–12** (12 = full
	    row, the default). Rendered on a 12-column grid — consecutive fields whose spans sum to ≤12
	    share a row. Legacy string values ('full'/'half'/'1/2'…) are still read (see fieldWidthSpan).
	    The builder edits this by drag; the field editor offers fraction presets. */
	width?: number | FieldWidth | 'half';
	/** Funding options: the checkbox sits in the left column and the fields it reveals stack in
	    the right. A span alone cannot do that — the next checkbox would sit beside a later field. */
	column?: 'left' | 'right';
	/** Label used instead of `field.label` on a pension or JISA, where the question stays
	    "contribution". Other wrappers say "payment in". */
	contributionLabel?: string;
	/** Button-group corners. The bank editor is square, matching the record; other groups stay pills. */
	shape?: 'pill' | 'square';
	/** Seed value when the field has no answer yet. Does not overwrite user input. */
	defaultValue?: unknown;
	/** Fill this field from a data-source row keyed by another field's value (see ComputedFrom).
	    Used for read-only `display` fields (e.g. show a selected account's wrapper) and prefills. */
	computedFrom?: ComputedFrom;
}

/** Named row fractions offered as quick presets (drag gives the full 1–12 range). */
export type FieldWidth = 'full' | '1/2' | '1/3' | '2/3' | '1/4' | '3/4';

/** The 12-column grid width. */
export const FIELD_WIDTH_MIN_SPAN = 1;
export const FIELD_WIDTH_MAX_SPAN = 12;

/** Column span (out of 12) for each named fraction. */
export const FIELD_WIDTH_SPAN: Record<FieldWidth, number> = {
	full: 12,
	'1/2': 6,
	'1/3': 4,
	'2/3': 8,
	'1/4': 3,
	'3/4': 9
};

/** Fraction presets for the field editor, in display order (label + resulting span). */
export const FIELD_WIDTHS: { value: FieldWidth; label: string; span: number }[] = [
	{ value: 'full', label: 'Full', span: 12 },
	{ value: '1/2', label: '½', span: 6 },
	{ value: '1/3', label: '⅓', span: 4 },
	{ value: '2/3', label: '⅔', span: 8 },
	{ value: '1/4', label: '¼', span: 3 },
	{ value: '3/4', label: '¾', span: 9 }
];

/** Resolve a stored width to a 1–12 column span. Accepts the numeric span (clamped), the legacy
    'half' alias, the named fractions, and absent/unknown values (→ full width). Used by the
    renderer and the builder layout. */
export function fieldWidthSpan(width: number | string | null | undefined): number {
	if (typeof width === 'number') {
		return Math.min(FIELD_WIDTH_MAX_SPAN, Math.max(FIELD_WIDTH_MIN_SPAN, Math.round(width)));
	}
	if (width === 'half') return FIELD_WIDTH_SPAN['1/2'];
	return FIELD_WIDTH_SPAN[width as FieldWidth] ?? FIELD_WIDTH_SPAN.full;
}

// Options union, mirrored from platform-1's FieldOptions.
export type FieldOptions =
	| null
	| string[]
	| Array<
			| string
			| { value: unknown; label?: unknown }
			| { code: unknown; name?: unknown; adjective?: unknown }
	  >
	| { options: unknown }
	| { source: string; valueKey?: string; labelKey?: string }
	| { currency?: string };

/** One column of a `repeating` field. Stored as the field's `options` list. `type` is a scalar
    from `REPEATING_COLUMN_TYPES`. `required` defaults to true when omitted. Choice columns carry
    their own `options` (the same shapes a select field uses). */
export interface RepeatingColumn {
	key: string;
	label: string;
	type: string;
	required?: boolean;
	options?: FieldOptions;
}

/** Points per option, and the bands the total falls into. Stored on the form, computed on submit. */
export interface FormScoringField {
	field_key: string;
	weights: Record<string, number>;
}

export interface FormScoringBand {
	key: string;
	label: string;
	min: number;
	max: number;
	definition: string;
}

export interface FormScoring {
	fields: FormScoringField[];
	bands: FormScoringBand[];
}

/** Written on submit. A posted answer under one of these keys is replaced with this value. */
export interface FormScoringOutputs {
	band: string | null;
	score: number;
	label: string | null;
	definition: string | null;
}

export interface FormField {
	/** public id (platform-1 used a numeric Field.id). */
	uid: string;
	/** the value/answer key (dotted paths allowed, e.g. `client.full_name`). */
	fieldKey: string;
	/** string-open so legacy aliases render; the builder offers FORM_FIELD_TYPES. */
	fieldType: string;
	label: string;
	section: string | null;
	description: string | null;
	required: boolean;
	/** preferred visibility source. */
	conditions: FieldConditions;
	validation: FieldValidation | null;
	options: FieldOptions | RepeatingColumn[];
	sortOrder: number;
}

export interface Form {
	uid: string;
	code: string;
	name: string;
	description?: string;
	status: FormStatus;
	entityType: FormEntityType;
	/** Optional document-type affinity — a *suggestion* for the template form picker,
	    never a lock (a form can be used by any template). See docs/forms.md §9.1. */
	documentType?: DocumentType;
	fieldCount: number;
	createdAt: string;
	createdBy: string;
	/** FUTURE (see docs/forms.md §9): the questionnaire dataset this form feeds. Not wired. */
	questionnaireDatasetCode?: string;
	/** Null when the form is not scored. See docs/forms.md §4.3. */
	scoring?: FormScoring | null;
}

/** A filled-in form — standalone mode (docs/forms.md §9). */
export interface FormResponse {
	uid: string;
	formUid: string;
	values: Record<string, unknown>;
	submittedAt: string | null;
	submittedBy: string | null;
}

export interface NewFormInput {
	name: string;
	code: string;
	entityType: FormEntityType;
	documentType?: DocumentType;
	description?: string;
}
