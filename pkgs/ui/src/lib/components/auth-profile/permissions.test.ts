import { describe, expect, it } from 'vitest';
import { groupPermissions } from './permissions';

describe('groupPermissions', () => {
	it('joins the verbs of one resource', () => {
		expect(groupPermissions(['actions:read', 'actions:write'])).toEqual(['actions:read,write']);
	});

	it('keeps read ahead of write, and a grant with no verb on its own', () => {
		expect(
			groupPermissions([
				'contacts:write',
				'system',
				'contacts:read_all',
				'contacts:read',
				'actions:read'
			])
		).toEqual(['actions:read', 'contacts:read,read_all,write', 'system']);
	});
});
