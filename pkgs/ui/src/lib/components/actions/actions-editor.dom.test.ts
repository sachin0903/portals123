/**
 * @vitest-environment jsdom
 *
 * The table says, on every row, whether the action waits on a second person and which roles
 * approve it, without opening the action.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import ActionsEditor from './actions-editor.svelte';
import type { ActionRow } from './actions-editor.svelte';
import type { Enhance } from '../book/clients/form-enhance.js';

const enhance = (() => ({ destroy() {} })) as unknown as Enhance;

afterEach(() => {
	document.body.innerHTML = '';
});

function row(key: string, name: string, approvers: string[]): ActionRow {
	return {
		key,
		name,
		description: '',
		category: 'money',
		subject_kinds: ['account'],
		providers: [],
		enabled: true,
		approval_enabled: approvers.length > 0,
		approval_role_slugs: approvers,
		role_slugs: []
	};
}

describe('ActionsEditor list', () => {
	it('names the approvers on every row, and says so when no approval is required', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const view = mount(ActionsEditor, {
			target,
			props: {
				categories: [{ id: 'money', label: 'Money' }],
				actions: [
					row('withdrawal', 'Withdraw cash', ['compliance', 'director']),
					row('deposit', 'Add cash', [])
				],
				roles: [
					{ slug: 'compliance', name: 'Compliance' },
					{ slug: 'director', name: 'Director' }
				],
				enhance
			}
		});

		// A row is the policy's own form (`data-action-row`), not a table row: the editor became a
		// flex list when it gained the in-place pickers and switches.
		const rows = [...target.querySelectorAll('[data-action-row]')];
		const withdraw = rows.find((el) => el.textContent?.includes('Withdraw cash'));
		const deposit = rows.find((el) => el.textContent?.includes('Add cash'));
		// The approvers are the picker's chips, by role name; an empty pick says "No approval".
		expect(withdraw?.textContent).toContain('Compliance');
		expect(withdraw?.textContent).toContain('Director');
		expect(withdraw?.textContent).not.toContain('No approval');
		expect(deposit?.textContent).toContain('No approval');

		unmount(view);
	});
});
