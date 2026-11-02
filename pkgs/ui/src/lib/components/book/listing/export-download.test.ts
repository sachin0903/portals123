import { describe, expect, it, vi } from 'vitest';
import { fetchExport } from './export-download';

const drawn = new Blob(['%PDF']);
vi.mock('./table-pdf.js', () => ({ tablePdf: vi.fn(async () => drawn) }));

const answer = (res: Response) => vi.fn(async () => res) as unknown as typeof fetch;

describe('fetchExport', () => {
	it('draws a PDF from the table the route sends, and saves it under its name', async () => {
		const save = vi.fn();
		const res = Response.json({
			filename: 'clients-2026-10-02.pdf',
			title: 'Clients',
			subtitle: '',
			headers: ['Client'],
			rows: [['Theo Caldwell']]
		});
		expect(await fetchExport('/clients/export?format=pdf', save, answer(res))).toEqual({
			kind: 'saved'
		});
		expect(save).toHaveBeenCalledWith(drawn, 'clients-2026-10-02.pdf');
	});

	it('saves the file under the name the route gave it', async () => {
		const save = vi.fn();
		const res = new Response('%PDF', {
			headers: { 'content-disposition': 'attachment; filename="clients-2026-10-02.pdf"' }
		});
		expect(await fetchExport('/clients/export?format=pdf', save, answer(res))).toEqual({
			kind: 'saved'
		});
		expect(save).toHaveBeenCalledWith(expect.any(Blob), 'clients-2026-10-02.pdf');
	});

	it('fails with the route’s own words, and saves nothing', async () => {
		const save = vi.fn();
		const res = Response.json(
			{ message: 'The PDF render service is unavailable. Export as CSV instead.' },
			{ status: 503 }
		);
		expect(await fetchExport('/x', save, answer(res))).toEqual({
			kind: 'failed',
			message: 'The PDF render service is unavailable. Export as CSV instead.'
		});
		expect(save).not.toHaveBeenCalled();
	});

	it('hands a sign-in redirect to the browser rather than saving it', async () => {
		const save = vi.fn();
		const res = new Response(null, { status: 303, headers: { location: '/login' } });
		expect(await fetchExport('/x', save, answer(res))).toEqual({ kind: 'navigate' });
		expect(save).not.toHaveBeenCalled();
	});
});
