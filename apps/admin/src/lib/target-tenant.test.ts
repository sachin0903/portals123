import { describe, expect, it } from 'vitest';
import { targetTenantUrl } from './target-tenant';

describe('targetTenantUrl', () => {
	it('preserves endpoint parameters and appends the route tenant', () => {
		const params = new URLSearchParams([
			['status', 'active'],
			['status', 'waiting']
		]);
		const url = new URL(targetTenantUrl('/tasks', 'ten_acme', params));
		expect(url.searchParams.getAll('status')).toEqual(['active', 'waiting']);
		expect(url.searchParams.get('tenant')).toBe('ten_acme');
	});

	it('does not let a caller replace the route tenant', () => {
		const url = new URL(
			targetTenantUrl('/documents', 'ten_target', { tenant: 'ten_wrong', limit: 200 })
		);
		expect(url.searchParams.get('tenant')).toBe('ten_target');
		expect(url.searchParams.get('limit')).toBe('200');
	});
});
