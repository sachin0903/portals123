/**
 * A Graphene role can carry ``system`` (the operations tenant may grant it) with no switch on
 * the platform tab. Say so, or turning Create clients off looks like it did nothing.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import RolesEditor from './roles-editor.svelte';
import type { RolesEditorPermission, RolesEditorRole } from './roles-editor.svelte';

const enhance = () => ({ destroy() {} });

const permissions: RolesEditorPermission[] = [
	{
		slug: 'contacts:create',
		name: 'Create clients',
		resource: 'contacts',
		portal: 'platform',
		description: 'Add a new client to the firm book.'
	},
	{
		slug: 'contacts:write',
		name: 'Edit clients',
		resource: 'contacts',
		portal: 'platform',
		description: 'Edit clients.'
	},
	{
		slug: 'system',
		name: 'System administration',
		resource: 'platform',
		portal: 'operations',
		description: 'Full Graphene internal-operations access.'
	},
	{
		slug: 'tenants:write',
		name: 'Manage tenants',
		resource: 'tenants',
		portal: 'operations',
		description: 'Edit firms.'
	}
];

const platformAdmin: RolesEditorRole = {
	slug: 'admin',
	name: 'Admin',
	description: null,
	portal: 'platform',
	permissions: ['contacts:write', 'system']
};

const operationsAdmin: RolesEditorRole = {
	slug: 'admin',
	name: 'Admin',
	description: null,
	portal: 'operations',
	permissions: ['system', 'tenants:write']
};

describe('RolesEditor (SSR)', () => {
	it('warns when a firm role also holds operations permissions that have no switch', () => {
		const body = render(RolesEditor, {
			props: {
				roles: [platformAdmin],
				permissions,
				form: null,
				enhance,
				portals: ['platform', 'operations']
			}
		}).body;
		expect(body).toContain('System administration');
		expect(body).toContain('do not appear here');
		expect(body).toContain('Create clients');
		expect(body).not.toContain('bypasses every other permission');
	});

	it('warns that system bypasses every other switch on the operations portal', () => {
		const body = render(RolesEditor, {
			props: {
				roles: [operationsAdmin],
				permissions,
				form: null,
				enhance,
				portals: ['operations']
			}
		}).body;
		expect(body).toContain('bypasses every other permission on this portal');
		expect(body).not.toContain('do not appear here');
	});
});
