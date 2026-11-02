import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import RolePicker from './role-picker.svelte';

const options = [
	{ value: 'admin', label: 'Administrator' },
	{ value: 'adviser', label: 'Adviser' },
	{ value: 'compliance', label: 'Compliance' }
];

describe('RolePicker (SSR)', () => {
	it('shows assigned roles inline before the menu is opened', () => {
		const body = render(RolePicker, {
			props: { options, value: ['admin', 'adviser'], ariaLabel: 'Edit roles for Alex' }
		}).body;

		expect(body).toContain('Administrator');
		expect(body).toContain('Adviser');
		expect(body).toContain('aria-label="Edit roles for Alex"');
	});

	it('collapses a long role set to a count', () => {
		const body = render(RolePicker, {
			props: { options, value: ['admin', 'adviser', 'compliance'], maxVisible: 1 }
		}).body;

		expect(body).toContain('Administrator');
		expect(body).toContain('+2');
	});
});
