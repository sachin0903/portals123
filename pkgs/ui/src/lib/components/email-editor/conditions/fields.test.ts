import { describe, expect, it } from 'vitest';
import { fieldFromToken, fieldOptions } from './fields';
import { MERGE_FIELD_GROUPS, mergeFieldsFor } from '../merge-fields';

describe('fieldFromToken', () => {
	it('takes the bare reference out of a token', () => {
		expect(fieldFromToken('{{ first_name }}')).toBe('first_name');
		expect(fieldFromToken('{{brand.support_email}}')).toBe('brand.support_email');
		expect(fieldFromToken('  {{ is_reminder }}  ')).toBe('is_reminder');
	});

	it('refuses a token carrying an EXPRESSION', () => {
		// `{{ first_name or 'there' }}` is fine to print and never falsey, so useless to ask about.
		expect(fieldFromToken("{{ first_name or 'there' }}")).toBeNull();
		expect(fieldFromToken('{{ message | paragraphs }}')).toBeNull();
	});

	it('refuses anything that is not a token', () => {
		expect(fieldFromToken('first_name')).toBeNull();
		expect(fieldFromToken('{% if x %}')).toBeNull();
		expect(fieldFromToken('')).toBeNull();
	});
});

describe('fieldOptions', () => {
	it('offers the template’s own vocabulary, in its order', () => {
		const options = fieldOptions(mergeFieldsFor('task_assigned'));
		const values = options.map((o) => o.value);
		expect(values).toContain('task_title');
		expect(values).toContain('button_url');
		// A task notice's send site passes no recipient name, so asking would be a rule always false.
		expect(values).not.toContain('first_name');
	});

	it('de-duplicates a field that appears in two groups', () => {
		const groups = [
			{ group: 'A', fields: [{ label: 'Name', token: '{{ brand.name }}' }] },
			{ group: 'B', fields: [{ label: 'Firm', token: '{{ brand.name }}' }] }
		];
		expect(fieldOptions(groups).map((o) => o.value)).toEqual(['brand.name']);
	});

	it('labels with the author’s word AND the reference', () => {
		const [first] = fieldOptions([
			{ group: 'A', fields: [{ label: 'First name', token: '{{ first_name }}' }] }
		]);
		expect(first).toEqual({ value: 'first_name', label: 'First name · first_name' });
	});

	it('drops the expression-bearing tokens rather than offering them', () => {
		const options = fieldOptions(MERGE_FIELD_GROUPS);
		expect(options.every((o) => !o.value.includes(' '))).toBe(true);
		expect(options.length).toBeGreaterThan(0);
	});
});
