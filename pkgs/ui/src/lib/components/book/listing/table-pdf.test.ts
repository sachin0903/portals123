import { readFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { tablePdf, type ExportTable } from './table-pdf';

const font = (name: string) => {
	const file = readFileSync(new URL(`./fonts/${name}`, import.meta.url));
	return file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength);
};

const table = (rows: number): ExportTable => ({
	filename: 'clients.pdf',
	title: 'Clients',
	subtitle: `${rows} rows`,
	headers: ['Client', 'Accounts', 'Adviser', 'Value', 'Status'],
	rows: Array.from({ length: rows }, (_, i) => [
		`Łukasz Montgomery-Fitzwilliam ${i}`,
		'GIA, ISA',
		'—',
		'£1,234,567.89',
		'Active'
	])
});

/** The PDF as text, its compressed streams inflated so the drawn words can be read. */
async function text(blob: Blob): Promise<string> {
	const raw = Buffer.from(await blob.arrayBuffer());
	const body = raw.toString('latin1');
	const streams = [...body.matchAll(/stream\r?\n/g)].map((m) => {
		const start = m.index + m[0].length;
		const chunk = raw.subarray(start, body.indexOf('endstream', start));
		try {
			return inflateSync(chunk).toString('latin1');
		} catch {
			return '';
		}
	});
	return [body, ...streams].join('\n');
}

describe('tablePdf', () => {
	it('lays out every row over as many pages as it takes, numbered', async () => {
		const pdf = await text(await tablePdf(table(300), { fonts: null }));
		expect(pdf.startsWith('%PDF')).toBe(true);
		expect(pdf).toContain('(Adviser)');
		expect(pdf).toContain('(Active)');
		const pages = (pdf.match(/\/Type \/Page\b/g) ?? []).length;
		expect(pages).toBeGreaterThan(1);
		expect(pdf).toContain(`(Page ${pages} of ${pages})`);
	});

	it('embeds Liberation Sans, so names past Latin-1 print as written', async () => {
		const fonts = {
			regular: font('LiberationSans-Regular.ttf'),
			bold: font('LiberationSans-Bold.ttf')
		};
		const pdf = await text(await tablePdf(table(3), { fonts }));
		expect(pdf).toContain('/LiberationSans');
	});
});
