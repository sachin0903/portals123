import { describe, expect, it } from 'vitest';
import { acceptFiles, acceptsType, fileKey, removeAt } from './files.js';

const PDF = 'application/pdf';
const PNG = 'image/png';

/** A stand-in for a picked file: only name/type/size/lastModified are ever read. */
function file(name: string, type = PDF, size = 1024, lastModified = 1_700_000_000_000): File {
	return new File([new Uint8Array(size)], name, { type, lastModified });
}

const OPTS = { accept: `${PDF},${PNG}`, maxBytes: 25 * 1024 * 1024, multiple: true };

describe('fileKey', () => {
	it('separates two different files that share a name', () => {
		// report.pdf from two folders is the ordinary case, and the rendered list is keyed on this.
		expect(fileKey(file('report.pdf', PDF, 10))).not.toBe(fileKey(file('report.pdf', PDF, 20)));
	});

	it('gives the same file the same key twice', () => {
		expect(fileKey(file('report.pdf'))).toBe(fileKey(file('report.pdf')));
	});
});

describe('acceptsType', () => {
	it('matches a family wildcard as well as an exact type', () => {
		expect(acceptsType(file('a.png', PNG), 'image/*')).toBe(true);
		expect(acceptsType(file('a.png', PNG), PNG)).toBe(true);
		expect(acceptsType(file('a.png', PNG), PDF)).toBe(false);
	});

	it('matches extensions case-insensitively when the browser supplies no MIME type', () => {
		expect(acceptsType(file('scan.HEIC', ''), '.heic,.pdf')).toBe(true);
		expect(acceptsType(file('old-statement.DOC', ''), '.doc,.docx')).toBe(true);
		expect(acceptsType(file('renamed.exe', ''), '.doc,.docx')).toBe(false);
	});

	it('accepts anything when no list is given', () => {
		expect(acceptsType(file('a.bin', 'application/octet-stream'), '')).toBe(true);
	});
});

describe('acceptFiles', () => {
	it('appends when multiple, so a second visit to the picker builds the set up', () => {
		// The picker only hands back what was chosen in THAT visit — replacing would make a drop
		// gathered from two folders impossible.
		const { files } = acceptFiles([file('a.pdf')], [file('b.pdf')], OPTS);
		expect(files.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf']);
	});

	it('replaces when single', () => {
		const { files } = acceptFiles([file('a.pdf')], [file('b.pdf')], { ...OPTS, multiple: false });
		expect(files.map((f) => f.name)).toEqual(['b.pdf']);
	});

	it('drops a file already chosen rather than listing it twice', () => {
		// Two entries with one key is a duplicate key, which Svelte throws on — so re-choosing a file
		// used to take the dialog down instead of being the no-op it looks like.
		const { files } = acceptFiles([file('a.pdf')], [file('a.pdf'), file('b.pdf')], OPTS);
		expect(files.map((f) => f.name)).toEqual(['a.pdf', 'b.pdf']);
		expect(new Set(files.map(fileKey)).size).toBe(files.length);
	});

	it('de-duplicates within one drop too', () => {
		const { files } = acceptFiles([], [file('a.pdf'), file('a.pdf')], OPTS);
		expect(files).toHaveLength(1);
	});

	it('keeps the good files and explains each rejection', () => {
		const { files, rejections } = acceptFiles(
			[],
			[file('ok.pdf', PDF, 16), file('bad.gif', 'image/gif'), file('big.pdf', PDF, 64)],
			{ ...OPTS, maxBytes: 32 }
		);
		expect(files.map((f) => f.name)).toEqual(['ok.pdf']);
		expect(rejections).toHaveLength(2);
		expect(rejections[0]).toMatch(/bad\.gif: unsupported file type/);
		expect(rejections[1]).toMatch(/big\.pdf: exceeds/);
	});

	it('leaves the selection alone when nothing usable arrived', () => {
		// A stray drop of the wrong type must not silently undo the files already chosen.
		const current = [file('a.pdf')];
		const { files, rejections } = acceptFiles(current, [file('bad.gif', 'image/gif')], OPTS);
		expect(files).toBe(current);
		expect(rejections).toHaveLength(1);
	});
});

describe('removeAt', () => {
	it('takes out the file at the index and keeps the order', () => {
		const files = [file('a.pdf'), file('b.pdf'), file('c.pdf')];
		expect(removeAt(files, 1).map((f) => f.name)).toEqual(['a.pdf', 'c.pdf']);
	});

	it('returns the list untouched for an index that is not in it', () => {
		const files = [file('a.pdf')];
		expect(removeAt(files, 3)).toBe(files);
		expect(removeAt(files, -1)).toBe(files);
	});

	it('empties the list when the last file is removed', () => {
		expect(removeAt([file('a.pdf')], 0)).toEqual([]);
	});
});
