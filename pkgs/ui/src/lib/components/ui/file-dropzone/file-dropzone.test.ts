import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * ONE size label and ONE formats line, used by every part of the control.
 *
 * Both halves were separately wrong and a browser QA pass caught each: the compact layout hardcoded
 * "PDF, JPEG, PNG or DOCX" so a caller accepting two image formats advertised four including a Word
 * document, and every size was rounded to MB — so a 512 KiB cap read as "1 MB" on the box AND in the
 * refusal a rejected file produced, contradicting the caller's real limit in both places.
 */
const source = readFileSync(join(import.meta.dirname, 'file-dropzone.svelte'), 'utf8');

describe('the stated limit is the enforced limit', () => {
	it('derives one label, from the shared helper', () => {
		// The KB arithmetic used to be inline here. It now comes from `files.ts::formatBytes`, which the
		// file list already used for a FILE's size — one function, so the cap and a file are measured the
		// same way. The trailing `.0` is dropped because a cap is a round number by construction.
		expect(source).toMatch(/const sizeLabel = \$derived\(formatBytes\(maxBytes\)/);
		expect(source).not.toMatch(/maxBytes \/ 1024 \/ 1024/);
	});

	it('states the cap in the refusal from the same helper, not its own arithmetic', () => {
		// The refusal lives in `files.ts::acceptFiles` now (extracted so it is testable without a DOM),
		// and it rounded to MB there — reporting a 512 KiB cap as "1 MB", the exact bug this file was
		// written for, in the one place a user reads it after being refused.
		const files = readFileSync(join(import.meta.dirname, 'files.ts'), 'utf8');
		expect(files).toMatch(/exceeds \$\{formatBytes\(maxBytes\)\}/);
		expect(files).not.toMatch(/Math\.round\(maxBytes \/ 1024 \/ 1024\)/);
	});

	it('honours the caller’s formats line in EVERY layout', () => {
		// Four now — rich, slim, icon and default. A hardcoded list in any one of them advertises formats
		// the caller does not accept, which is the bug this started as. `icon` puts it in the tooltip.
		expect(source.match(/formats \?\? /g)?.length).toBe(4);
	});

	it('offers the common document and raster-image families by default', () => {
		for (const extension of [
			'.pdf',
			'.docx',
			'.xlsx',
			'.pptx',
			'.odt',
			'.txt',
			'.csv',
			'.rtf',
			'.jpg',
			'.png',
			'.gif',
			'.webp',
			'.tiff',
			'.heic',
			'.avif'
		]) {
			expect(source).toContain(extension);
		}
	});

	it('offers a SLIM compact stack and an ICON chip for a one-line rail row', () => {
		expect(source).toMatch(/'default' \| 'slim' \| 'icon'/);
		// Title then limit — a single row truncated the formats mid-sentence ("up to 512…").
		expect(source).toMatch(/slim: 'flex w-full flex-col/);
		expect(source).toMatch(/icon: 'flex size-8/);
		// `rich` still wins: it is a whole presentation, not a size.
		expect(source).toMatch(/size: rich \? 'default' : size/);
	});
});
