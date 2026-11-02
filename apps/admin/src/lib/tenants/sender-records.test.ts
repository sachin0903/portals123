import { describe, expect, it } from 'vitest';
import { recordsCsv, requiredRecordsVerified, type DnsRecord } from './sender-records';

const row = (over: Partial<DnsRecord>): DnsRecord => ({
	purpose: 'SPF',
	name: 'send.mail.example.co.uk',
	type: 'TXT',
	value: 'v=spf1 include:amazonses.com ~all',
	ttl: 'Auto',
	status: 'verified',
	priority: null,
	required: true,
	...over
});

describe('sending DNS records', () => {
	it('treats the setup as done only when every required record is verified', () => {
		const dmarc = row({ purpose: 'DMARC', required: false, status: 'optional' });
		expect(requiredRecordsVerified([row({}), row({ purpose: 'DKIM' }), dmarc])).toBe(true);
		expect(requiredRecordsVerified([row({ status: 'pending' }), dmarc])).toBe(false);
		expect(requiredRecordsVerified([dmarc])).toBe(false);
	});

	it('puts the required records and the optional DMARC row in one file', () => {
		const file = recordsCsv('mail.example.co.uk', [
			row({ purpose: 'DKIM', value: 'p=abc,def' }),
			row({
				purpose: 'DMARC',
				name: '_dmarc.mail.example.co.uk',
				value: 'v=DMARC1; p=none;',
				required: false,
				status: 'optional'
			})
		]);
		expect(file.filename).toBe('mail.example.co.uk-dns-records.csv');
		expect(file.body.split('\r\n')[0]).toBe('purpose,type,name,value,priority,ttl');
		expect(file.body).toContain('"p=abc,def"');
		expect(file.body).toContain('v=DMARC1; p=none;');
	});
});
