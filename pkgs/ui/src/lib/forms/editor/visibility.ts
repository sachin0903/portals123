import type { FormField } from '../types.js';

// Lifted verbatim from platform-1 `pkgs/fields/src/runtime/visibility.ts`
// (behaviour and its unit tests carried across). Pure, framework-free.

/**
 * Gets a nested value from an object using dot notation (e.g. "address.line1").
 * Also supports cross-entity paths via `application.`/`applicant.`/`account.` prefixes.
 */
export function getNestedValue(
	obj: Record<string, unknown>,
	path: string,
	applicationData?: Record<string, unknown>
): unknown {
	if (!path) return undefined;

	// A dotted FIELD KEY is one key, not a path. The form keeps its answers flat by `fieldKey`
	// (`values['pension.hasEmployerContributions']`), so a condition naming that key must find the
	// answer before it is read as `values.pension.hasEmployerContributions` — which is nothing, and
	// nothing reads as "no". The seeded employer step revealed its company fields on exactly that
	// condition, so the tick never showed them; the API's mapper resolves the same way
	// (`_answer_for`: exact key first, then the nested walk).
	if (Object.prototype.hasOwnProperty.call(obj, path)) {
		return (obj as Record<string, unknown>)[path];
	}

	if (path.startsWith('application.')) {
		const appPath = path.replace('application.', '');
		return getNestedValue(applicationData || {}, appPath);
	}

	// Applicant-scoped alias. In applicant contexts the "data" object is the applicant object/data,
	// so `applicant.x` resolves against `obj`. For account contexts, fall back to
	// `applicationData.applicant` which is populated with the current applicant's data.
	if (path.startsWith('applicant.')) {
		const applicantPath = path.replace('applicant.', '');
		const fromObj = getNestedValue(obj, applicantPath, applicationData);
		if (fromObj !== undefined) return fromObj;
		// Fallback: resolve from applicationData.applicant (for cross-entity conditions)
		const applicantData =
			applicationData && typeof applicationData === 'object'
				? (applicationData as Record<string, unknown>).applicant
				: undefined;
		if (applicantData && typeof applicantData === 'object') {
			return getNestedValue(applicantData as Record<string, unknown>, applicantPath);
		}
		return undefined;
	}

	// Support legacy/explicit account prefixes in conditions and field keys.
	// For account-scoped fields, prefer the provided data object, then fallback to applicationData.
	//
	// The LAST fallback is the one that matters today and was missing, where `applicant.` has had
	// it all along: `GET /applications/{uid}/steps/…/form` sends the account facts NESTED, as
	// `context.account = { product_type, custody, … }`. Stripping the prefix and looking `product_type`
	// up flat finds nothing in that shape, so every `account.*` condition resolved to undefined —
	// which reads as "no". The seeded onboarding flow conditions on `account.product_type` and
	// `account.custody`, so its pension-only questions were never asked and its not-a-pension ones
	// were always asked.
	if (path.startsWith('account.')) {
		const accountPath = path.replace('account.', '');
		const fromData = getNestedValue(obj, accountPath, applicationData);
		if (fromData !== undefined) return fromData;
		const flat = getNestedValue(applicationData || {}, accountPath);
		if (flat !== undefined) return flat;
		for (const source of [obj, applicationData]) {
			const account = source && typeof source === 'object' ? source.account : undefined;
			if (account && typeof account === 'object') {
				const nested = getNestedValue(account as Record<string, unknown>, accountPath);
				if (nested !== undefined) return nested;
			}
		}
		return undefined;
	}

	const keys = path.split('.');
	let current: unknown = obj;

	for (const key of keys) {
		if (current && typeof current === 'object' && key in (current as Record<string, unknown>)) {
			current = (current as Record<string, unknown>)[key];
		} else {
			return undefined;
		}
	}

	return current;
}

type RawCondition = {
	field?: string;
	operator?: string;
	value?: unknown;
	logic?: string;
	conditions?: RawCondition[];
};

function evaluateNode(
	node: unknown,
	data: Record<string, unknown>,
	applicationData?: Record<string, unknown>
): boolean {
	if (node && typeof node === 'object' && Array.isArray((node as RawCondition).conditions)) {
		const group = node as RawCondition;
		const logic = String(group.logic || 'AND').toUpperCase();
		const results = (group.conditions ?? []).map((child) =>
			evaluateNode(child, data, applicationData)
		);
		return logic === 'OR' ? results.some(Boolean) : results.every(Boolean);
	}
	return evaluateCondition(node as RawCondition, data, applicationData);
}

function evaluateCondition(
	cond: RawCondition,
	data: Record<string, unknown>,
	applicationData?: Record<string, unknown>
): boolean {
	if (!cond.field || !cond.operator) return true;
	const fieldValue = getNestedValue(data, cond.field, applicationData);
	const op = String(cond.operator);

	switch (op) {
		case 'equals':
		case '===':
			return fieldValue === cond.value;
		case 'notEquals':
		case '!==':
			return fieldValue !== cond.value;
		case 'in':
			return Array.isArray(cond.value) && (cond.value as unknown[]).includes(fieldValue);
		case 'notIn':
			// If cond.value is not an array, we can't determine if fieldValue is "in" it,
			// so logically "not in nothing" should be true.
			if (!Array.isArray(cond.value)) return true;
			return !(cond.value as unknown[]).includes(fieldValue);
		case 'contains':
			// Handle null/undefined — they don't "contain" anything.
			if (fieldValue === null || fieldValue === undefined) return false;
			return String(fieldValue).includes(String(cond.value));
		case 'notContains':
			// Handle null/undefined — they trivially "do not contain" any value.
			if (fieldValue === null || fieldValue === undefined) return true;
			return !String(fieldValue).includes(String(cond.value));
		case 'isEmpty':
			// Consider null, undefined, empty string, and empty arrays as "empty".
			if (fieldValue === null || fieldValue === undefined || fieldValue === '') return true;
			if (Array.isArray(fieldValue) && fieldValue.length === 0) return true;
			return false;
		case 'isNotEmpty':
			// Inverse of isEmpty.
			if (fieldValue === null || fieldValue === undefined || fieldValue === '') return false;
			if (Array.isArray(fieldValue) && fieldValue.length === 0) return false;
			return true;
		default:
			return true;
	}
}

/**
 * Canonical visibility is driven by `field.conditions`. We also support
 * `field.validation.conditional` when present, to avoid dropping conditional
 * functionality where legacy data used validation.
 */
export function isFieldVisible(
	field: Pick<FormField, 'conditions' | 'validation'>,
	data: Record<string, unknown>,
	applicationData?: Record<string, unknown>
): boolean {
	const rawConditions = (field.conditions ?? null) as unknown;
	const rawValidation = (field.validation ?? null) as unknown;

	// Prefer explicit `conditions` if present.
	if (rawConditions) {
		const raw = rawConditions as
			| RawCondition
			| { conditional?: RawCondition | { logic?: 'AND' | 'OR'; conditions?: RawCondition[] } };

		if ('conditional' in raw && raw.conditional) {
			const conditional = raw.conditional as unknown;
			if (
				conditional &&
				typeof conditional === 'object' &&
				'conditions' in (conditional as Record<string, unknown>) &&
				Array.isArray((conditional as Record<string, unknown>).conditions)
			) {
				const conditionalObj = conditional as Record<string, unknown>;
				return evaluateNode(conditionalObj, data, applicationData);
			}
			return evaluateNode(conditional, data, applicationData);
		}

		return evaluateNode(raw, data, applicationData);
	}

	const conditional =
		rawValidation &&
		typeof rawValidation === 'object' &&
		'conditional' in (rawValidation as Record<string, unknown>)
			? (rawValidation as Record<string, unknown>).conditional
			: undefined;
	if (!conditional) return true;

	if (
		conditional &&
		typeof conditional === 'object' &&
		'conditions' in (conditional as Record<string, unknown>) &&
		Array.isArray((conditional as Record<string, unknown>).conditions)
	) {
		return evaluateNode(conditional, data, applicationData);
	}

	return evaluateNode(conditional, data, applicationData);
}
