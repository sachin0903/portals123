import { describe, expect, it } from 'vitest';
import { connectionDetail, connectionStatus, type PlatformStatus } from './registry';

const status = (service: PlatformStatus['service']): PlatformStatus => ({
	provider: 'sei',
	meta: {
		name: 'SEI',
		role: 'custodian',
		description: '',
		transports: [],
		source_label: '',
		kpis: []
	},
	service,
	enabled_firms: 1
});

describe('platform index status', () => {
	it('names a daemon whose startup apply is still running', () => {
		expect(connectionStatus('starting')).toBe('degraded');
		expect(connectionDetail(status('starting'))).toBe('Data service starting · 1 enabled firm');
	});
});
