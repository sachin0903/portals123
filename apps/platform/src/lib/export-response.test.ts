import { describe, expect, it, vi } from 'vitest';
import { unzipSync, strFromU8 } from 'fflate';
import { exportResponse, sheetName, PDF_MAX_ROWS } from './export-response';

// Every case here BUILDS A REAL WORKBOOK — zip compression plus the XML parts — and the first one
// also pays fflate's cold import. That is a few hundred milliseconds idle and over vitest's 5s
// default when the machine is busy: under the pre-push hook, which runs the API's whole suite
// alongside this, it took 7.5s and failed as a timeout. A timeout that reports "the workbook is not
// a real xlsx" when the workbook is fine is worse than no assertion, so the budget is stated to
// match the work rather than left at a default sized for pure functions.
vi.setConfig({ testTimeout: 30_000 });

const SPEC = {
	listing: 'account-transactions',
	filename: 'account-12-transactions-2026-08-27',
	title: 'Theo Caldwell ISA — transactions',
	subtitle: '583 rows',
	headers: ['Date', 'Reference', 'Units', 'Amount'] as const,
	types: ['date', 'text', 'number', 'money'] as const,
	rows: [['2026-08-20', '000839969', 1111.09, 3019372.74]]
};

type Spec = Parameters<typeof exportResponse>[1];

/** Records into `recorded` rather than reaching an API. */
function recorder() {
	const recorded: unknown[] = [];
	const fetch = (async (_url: string, init?: RequestInit) => {
		recorded.push(JSON.parse(String(init?.body)));
		return new Response(null, { status: 204 });
	}) as unknown as typeof globalThis.fetch;
	return { recorded, recorder: { fetch, accessToken: 'tok' } };
}

/** The sheet's XML and the shared-string table, out of the .xlsx zip. */
async function open(res: Response) {
	const files = unzipSync(new Uint8Array(await res.arrayBuffer()));
	const read = (name: string) => (files[name] ? strFromU8(files[name]) : '');
	return { sheet: read('xl/worksheets/sheet1.xml'), strings: read('xl/sharedStrings.xml'), files };
}

const write = (spec = SPEC) => exportResponse('xlsx', spec as unknown as Spec, recorder().recorder);

/**
 * A generous timeout on the workbook suite.
 *
 * `exportResponse` imports `write-excel-file/node` LAZILY, so the first xlsx test in a run pays
 * Vite's cold transform of a zip library. Alone that is fast; inside the full suite, competing for
 * a Windows box's IO, it is not — this suite's first test failed once in three full runs and never
 * when run on its own, which is the shape of a timeout rather than of a defect. `pkgs/ui` records
 * the same trap for pulling bits-ui through the cold transform.
 *
 * A mitigation, stated as one: the failure was not captured, so this is the most likely cause
 * rather than a diagnosed one. If it recurs WITH this timeout, the cause is something else.
 */
const COLD_IMPORT_MS = 30_000;

describe('the workbook', () => {
	it('is a real xlsx, named and never cached', { timeout: COLD_IMPORT_MS }, async () => {
		const res = await write();
		expect(res.headers.get('content-type')).toBe(
			'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
		);
		expect(res.headers.get('content-disposition')).toContain(
			'account-12-transactions-2026-08-27.xlsx'
		);
		expect(res.headers.get('cache-control')).toBe('private, no-store');
		const { files } = await open(res);
		// A workbook Excel will open, not a zip of hopeful XML.
		expect(Object.keys(files)).toEqual(
			expect.arrayContaining(['xl/workbook.xml', '[Content_Types].xml'])
		);
	});

	it('keeps a leading-zero reference as TEXT', async () => {
		// THE reason column types are declared and never inferred. `000839969` is a real reference on
		// this book; as a number it becomes 839969 and matches nothing at the custodian.
		const { strings } = await open(await write());
		expect(strings).toContain('000839969');
	});

	it('writes money and units as numbers, so they sum and sort', async () => {
		const { sheet } = await open(await write());
		// Numeric cells carry the value inline; a text cell would be a shared-string index instead.
		expect(sheet).toContain('3019372.74');
		expect(sheet).toContain('1111.09');
	});

	it('writes the date as a date, not a string', async () => {
		// Excel serial for 2026-08-20 — a real date sorts chronologically and answers date filters,
		// which an ISO string does not.
		const { sheet, strings } = await open(await write());
		expect(strings).not.toContain('2026-08-20');
		expect(sheet).toMatch(/<c[^>]*s="\d+"[^>]*><v>4\d{4}<\/v>/);
	});

	it('freezes the header row', async () => {
		// On a 583-row ledger this is the difference between a table and a wall of numbers.
		const { sheet } = await open(await write());
		expect(sheet).toContain('<pane');
		expect(sheet).toContain('ySplit="1"');
	});

	it('gives every column a width, so a date is never ########', async () => {
		// The complaint that got this format built: CSV has no formatting layer, so Excel opens it at
		// the default 8.43 characters and a formatted date does not fit.
		const { sheet } = await open(await write());
		const cols = [...sheet.matchAll(/<col [^>]*width="([\d.]+)"/g)].map((m) => Number(m[1]));
		expect(cols).toHaveLength(SPEC.headers.length);
		expect(Math.min(...cols)).toBeGreaterThanOrEqual(10);
	});

	it('leaves an untyped column as text', async () => {
		// The default has to be the safe one: text cannot corrupt a value, a wrong number can.
		const { strings } = await open(
			await write({ ...SPEC, types: undefined as never, rows: [['2026-08-20', '007', 1, 2]] })
		);
		expect(strings).toContain('007');
	});
});

describe('the worksheet name', () => {
	// `write-excel-file` THROWS on these, so an unsanitised name is a 500, not a cosmetic defect.
	// The book contains `MLSIPP/01417` and account names like `Smith / Jones Trust`, and the title
	// this is built from is `{account name} — transactions`, so it is reached by ordinary data.
	it.each([
		['Smith / Jones Trust — holdings', 'Smith Jones Trust — holdings'],
		['ISA[GIA]', 'ISA GIA'],
		[String.raw`a:b*c?d\e/f`, 'a b c d e f']
	])('strips what Excel forbids: %s', (title, expected) => {
		expect(sheetName(title)).toBe(expected);
	});

	it('never returns something Excel refuses', () => {
		// Blank, over-long, and apostrophe-edged are all rejected by Excel itself.
		expect(sheetName('///')).toBe('Export');
		expect(sheetName('')).toBe('Export');
		expect(sheetName('x'.repeat(80))).toHaveLength(31);
		expect(sheetName("'quoted'")).toBe('quoted');
	});

	it('writes a workbook for a title that used to throw', async () => {
		const res = await exportResponse(
			'xlsx',
			{
				...SPEC,
				title: 'MLSIPP/01417 — transactions'
			} as unknown as Spec,
			recorder().recorder
		);
		expect(res.status).toBe(200);
		const { files } = await open(res);
		expect(files['xl/worksheets/sheet1.xml']).toBeTruthy();
	});
});

describe('the PDF', () => {
	it('is JSON of only the on-screen columns, named for a PDF', async () => {
		// Reference is sheet-only here, the way a real route declares it — the PDF must drop it
		// rather than carry every column the workbook does.
		const res = await exportResponse(
			'pdf',
			{ ...SPEC, sheetOnly: ['Reference'] } as unknown as Spec,
			recorder().recorder
		);
		const table = await res.json();
		expect(table.filename).toBe('account-12-transactions-2026-08-27.pdf');
		expect(table.headers).toEqual(['Date', 'Units', 'Amount']);
		expect(table.rows[0]).toEqual(['2026-08-20', '1111.09', '3019372.74']);
	});

	it('refuses over PDF_MAX_ROWS rather than quietly truncating', async () => {
		const spec = { ...SPEC, rows: Array.from({ length: PDF_MAX_ROWS + 1 }, () => SPEC.rows[0]) };
		const { recorded, recorder: r } = recorder();
		await expect(exportResponse('pdf', spec as unknown as Spec, r)).rejects.toMatchObject({
			status: 422
		});
		// Nothing left the platform, so nothing is recorded.
		expect(recorded).toEqual([]);
	});
});

describe('the audit record', () => {
	it('records every export with its listing, format and row count', async () => {
		const { recorded, recorder: r } = recorder();
		await exportResponse('csv', SPEC as unknown as Spec, r);
		expect(recorded).toEqual([{ listing: 'account-transactions', format: 'csv', rows: 1 }]);
	});
});
