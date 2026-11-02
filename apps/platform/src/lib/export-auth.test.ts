import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { listingExportHref, canExportListings } from './export-auth';

const ROUTES = join(import.meta.dirname, '..', 'routes');

function walk(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...walk(path));
		else out.push(path);
	}
	return out;
}

describe('listing export permission', () => {
	it('is not implied by viewing or editing the book', () => {
		expect(canExportListings(['contacts:read'], null)).toBe(false);
		expect(canExportListings(['contacts:write'], null)).toBe(false);
		expect(canExportListings(['listings:export'], null)).toBe(true);
	});

	it('hides the export URL when the grant is missing', () => {
		expect(listingExportHref(['contacts:read'], null, '/clients/export')).toBeUndefined();
		expect(listingExportHref(['listings:export'], null, '/clients/export')).toBe('/clients/export');
	});

	it('gates every export +server.ts through requireListingExport', () => {
		const servers = walk(ROUTES).filter((f) => f.endsWith(`${join('export', '+server.ts')}`));
		expect(servers.length).toBeGreaterThan(0);
		for (const file of servers) {
			const src = readFileSync(file, 'utf8');
			// Management reports are not a book listing. They download under reports:export.
			if (src.includes("'reports:export'")) continue;
			// A flow export is configuration — the graph and the forms it names, no client data —
			// so it downloads under the right to read flows, not the right to take the book off.
			if (src.includes("'flows:read'")) continue;
			expect(src, file).toContain('requireListingExport');
		}
	});

	it('hides every listing Export button behind listingExportHref', () => {
		const pages = walk(ROUTES)
			.filter((f) => f.endsWith('+page.svelte') || f.endsWith('account-record.svelte'))
			.filter((f) => {
				if (!statSync(f).isFile()) return false;
				return /exportHref|ExportHref/.test(readFileSync(f, 'utf8'));
			});
		// The shared account record lives next to this helper, not under routes.
		pages.push(join(import.meta.dirname, 'components', 'accounts', 'account-record.svelte'));
		expect(pages.length).toBeGreaterThan(0);
		for (const file of pages) {
			const src = readFileSync(file, 'utf8');
			expect(src, file).toContain('listingExportHref');
		}
	});
});
