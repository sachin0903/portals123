import type { ExportTable } from './table-pdf.js';

/**
 * Fetching a listing export in place, so the listing stays on screen while the file builds and a
 * failure reads as a message. As a plain navigation, a failed export replaced the listing with a
 * bare error page, and a slow one showed nothing at all until the file arrived.
 */

export type ExportOutcome =
	| { kind: 'saved' }
	/** Not a file: a sign-in redirect. The browser follows it, as a plain link would have. */
	| { kind: 'navigate' }
	| { kind: 'failed'; message: string };

const FALLBACK = 'The export failed. Try again, or choose another format.';

/** `attachment; filename="clients-2026-10-02.pdf"` → `clients-2026-10-02.pdf`. */
export function attachmentName(disposition: string | null, fallback: string): string {
	return /filename="?([^";]+)"?/i.exec(disposition ?? '')?.[1] ?? fallback;
}

/** `fallback` is the message when the server gives none: the export's own, unless a caller that
 *  is not an export names its file. */
export async function fetchExport(
	url: string,
	save: (file: Blob, name: string) => void,
	fetcher: typeof fetch = fetch,
	fallback: string = FALLBACK
): Promise<ExportOutcome> {
	let res: Response;
	try {
		// `manual`, because a lapsed session redirects to a sign-in page — saving that as
		// `clients.pdf` would be worse than the error page this replaces.
		res = await fetcher(url, { redirect: 'manual', headers: { accept: 'application/json, */*' } });
	} catch {
		return { kind: 'failed', message: fallback };
	}
	if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
		return { kind: 'navigate' };
	}
	if (!res.ok) {
		// SvelteKit's `error()` answers with `{ message }`: the route's own words, which already say
		// what to do instead ("Export as CSV instead").
		const body = (await res.json().catch(() => null)) as { message?: string } | null;
		return { kind: 'failed', message: body?.message || fallback };
	}
	// A PDF arrives as the table itself and is drawn here, in the browser (`table-pdf.ts`).
	if (res.headers.get('content-type')?.includes('application/json')) {
		const table = (await res.json()) as ExportTable;
		const { tablePdf } = await import('./table-pdf.js');
		save(await tablePdf(table), table.filename);
		return { kind: 'saved' };
	}
	save(await res.blob(), attachmentName(res.headers.get('content-disposition'), 'export'));
	return { kind: 'saved' };
}

/** The browser's own save dialog, for a file already in memory. */
export function saveFile(file: Blob, name: string): void {
	const href = URL.createObjectURL(file);
	const link = Object.assign(document.createElement('a'), { href, download: name });
	document.body.append(link);
	link.click();
	link.remove();
	// Not revoked at once: some browsers cancel a download whose URL goes away mid-start.
	setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
