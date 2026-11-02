import { describe, expect, it } from 'vitest';
import { canCloseClient, clientStatus } from './status.js';

describe('clientStatus', () => {
	it('is derived, not stored: no account ⇒ lead, else active', () => {
		expect(clientStatus({ account_count: 3 })).toBe('active');
		expect(clientStatus({ account_count: 0 })).toBe('lead');
		// A JISA registered contact acts on the child's account without owning it. That makes the
		// relationship active but must not put the child's account or AUM on the adult's own row.
		expect(clientStatus({ account_count: 0, represented_account_count: 1 })).toBe('active');
	});

	it('Closed wins: a closed client is not a lead even with no accounts', () => {
		expect(clientStatus({ account_count: 0, deleted_at: '2026-09-11T00:00:00Z' })).toBe('closed');
		expect(clientStatus({ account_count: 2, deleted_at: '2026-09-11T00:00:00Z' })).toBe('closed');
	});

	it('Suspended comes from the custodian, never from enabled', () => {
		expect(clientStatus({ account_count: 2, provider_status: 'suspended' })).toBe('suspended');
		expect(clientStatus({ account_count: 2, provider_status: 'Suspended' })).toBe('suspended');
		expect(clientStatus({ account_count: 0, provider_status: 'inactive' })).toBe('suspended');
		expect(clientStatus({ account_count: 2, provider_status: 'active' })).toBe('active');
		expect(clientStatus({ account_count: 2, provider_status: 'pending' })).toBe('active');
	});
});

describe('canCloseClient', () => {
	it('is true when there are no accounts, or every account is worth zero', () => {
		expect(canCloseClient([])).toBe(true);
		expect(canCloseClient([{ value: 0 }, { value: null }])).toBe(true);
		expect(canCloseClient([{ value: 1 }])).toBe(false);
		expect(canCloseClient([{ value: 0 }, { value: 50 }])).toBe(false);
	});
});
