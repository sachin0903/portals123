import type { FormField } from '../types.js';
import { validateRepeating } from '../repeating.js';

// The react-hook-form `baseRules.validate` logic from platform-1's
// dynamic-field-renderer, reimplemented as a pure function so the Svelte
// renderer can validate without RHF. Returns an error message, or null if valid.

export function isEmptyValue(value: unknown): boolean {
	if (value === undefined || value === null) return true;
	if (typeof value === 'string' && value.trim() === '') return true;
	if (Array.isArray(value) && value.length === 0) return true;
	return false;
}

export function validateField(
	field: Pick<FormField, 'fieldType' | 'label' | 'required' | 'validation'> & {
		options?: FormField['options'];
	},
	value: unknown
): string | null {
	const validation = field.validation ?? null;
	const label = field.label;

	if (field.fieldType === 'repeating') {
		return validateRepeating({ ...field, options: field.options ?? null }, value);
	}

	// Empty handling: optional-and-empty is valid; required-and-empty fails.
	if (isEmptyValue(value)) {
		return field.required ? `${label} is required` : null;
	}

	if (field.fieldType === 'address') {
		if (!value || typeof value !== 'object' || Array.isArray(value)) {
			return 'Enter a complete address';
		}
		const address = value as Record<string, unknown>;
		const complete = ['line1', 'city', 'postcode', 'country'].every(
			(key) => typeof address[key] === 'string' && address[key].trim().length >= 2
		);
		if (!complete) return 'Enter the address line, city, postal code and country';
	}

	// String-length + pattern checks (only meaningful for string values).
	if (typeof value === 'string') {
		if (validation?.minLength && value.length < validation.minLength) {
			return validation.errorMessage || `Minimum ${validation.minLength} characters required`;
		}
		if (validation?.maxLength && value.length > validation.maxLength) {
			return validation.errorMessage || `Maximum ${validation.maxLength} characters allowed`;
		}
		if (validation?.pattern && value.length > 0) {
			let ok = true;
			try {
				ok = new RegExp(validation.pattern).test(value);
			} catch {
				ok = true; // an invalid author-supplied pattern never blocks the user
			}
			if (!ok) return validation.patternMessage || validation.errorMessage || 'Invalid format';
		}
	}

	// Numeric bounds.
	if (field.fieldType === 'number') {
		const num = typeof value === 'number' ? value : Number(String(value));
		if (!Number.isFinite(num)) return validation?.errorMessage || 'Must be a number';
		if (validation?.min !== undefined && num < Number(validation.min)) {
			return validation?.errorMessage || `Minimum value is ${validation.min}`;
		}
		if (validation?.max !== undefined && num > Number(validation.max)) {
			return validation?.errorMessage || `Maximum value is ${validation.max}`;
		}
	}

	return null;
}
