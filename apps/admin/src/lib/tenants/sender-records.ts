import { csvDocument } from '$lib/csv';

/**
 * The DNS rows a firm is asked to publish, and the file operations sends them.
 *
 * SPF and DKIM are what Resend checks. Once every one of those is verified the settings page
 * stops presenting them as work still to do. DMARC is optional — Resend does not check it, and
 * it stays in the file so the firm can publish it when they want the extra policy.
 */

export type DnsRecord = {
	purpose: string;
	name: string;
	type: string;
	value: string;
	ttl: string | null;
	status: string;
	priority: number | null;
	required: boolean;
};

export function requiredRecordsVerified(records: readonly DnsRecord[]): boolean {
	const required = records.filter((record) => record.required);
	return required.length > 0 && required.every((record) => record.status === 'verified');
}

const COLUMNS = ['purpose', 'type', 'name', 'value', 'priority', 'ttl'] as const;

/** A CSV a firm can import or hand to whoever runs their DNS. */
export function recordsCsv(
	domain: string,
	records: readonly DnsRecord[]
): { filename: string; body: string } {
	const body = csvDocument(
		COLUMNS,
		records.map((record) => ({
			purpose: record.purpose,
			type: record.type,
			name: record.name,
			value: record.value,
			priority: record.priority ?? '',
			ttl: record.ttl ?? ''
		}))
	);
	return { filename: `${domain}-dns-records.csv`, body };
}

export function downloadRecords(domain: string, records: readonly DnsRecord[]): void {
	const file = recordsCsv(domain, records);
	const blob = new Blob([file.body], { type: 'text/csv;charset=utf-8' });
	const url = URL.createObjectURL(blob);
	const link = document.createElement('a');
	link.href = url;
	link.download = file.filename;
	link.click();
	URL.revokeObjectURL(url);
}
