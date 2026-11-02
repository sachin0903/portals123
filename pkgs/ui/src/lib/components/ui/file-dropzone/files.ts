/** The dropzone's selection rules, kept out of the component so they can be unit-tested without
 * mounting anything (the same split as `data-table/sort.ts` and `money-input/format.ts`). */

import { formatBytes } from '../../../bytes.js';

/** Identity of a chosen file.
 *
 * The rendered list is keyed on this, so it has to separate two genuinely different files that
 * share a name — `report.pdf` from two folders is the ordinary case. Name alone made a repeat
 * selection a DUPLICATE KEY, which Svelte throws on, so choosing the same file twice used to take
 * the dialog down rather than being the no-op it looks like. */
export function fileKey(file: File): string {
	return `${file.name}:${file.size}:${file.lastModified}`;
}

/** Does `file` satisfy a comma-separated browser `accept` list? Supports exact MIME types,
 * `type/*` families and `.extension` entries. The extension path is load-bearing for HEIC and
 * legacy Office files, whose browser-supplied `File.type` is commonly blank. */
export function acceptsType(file: File, accept: string): boolean {
	const list = accept
		.split(',')
		.map((a) => a.trim())
		.filter(Boolean);
	if (list.length === 0) return true;
	const type = file.type.toLowerCase();
	const name = file.name.toLowerCase();
	return list.some((raw) => {
		const accepted = raw.toLowerCase();
		if (accepted.startsWith('.')) return name.endsWith(accepted);
		return accepted.endsWith('/*') ? type.startsWith(accepted.slice(0, -1)) : type === accepted;
	});
}

/** What a drop or a picker choice does to the current selection.
 *
 * `multiple` APPENDS, because the picker only ever hands back the files chosen in that one visit —
 * so a second visit has to add to the set rather than replace it, which is what lets someone build
 * a drop up from several folders. The consequence is that a file already in the set must be
 * dropped: re-choosing it is a no-op, not a second copy of the same upload.
 *
 * Rejections are RETURNED rather than thrown, one reason per file, so a mixed drop keeps the files
 * that were fine and still explains the ones it did not take. */
export function acceptFiles(
	current: File[],
	incoming: File[],
	options: { accept: string; maxBytes: number; multiple: boolean }
): { files: File[]; rejections: string[] } {
	const { accept, maxBytes, multiple } = options;
	const rejections: string[] = [];
	const taken: File[] = [];
	// Only meaningful when appending; a single-file zone replaces whatever was there.
	const seen = new Set(multiple ? current.map(fileKey) : []);
	for (const file of incoming) {
		if (!acceptsType(file, accept)) {
			rejections.push(`${file.name}: unsupported file type`);
			continue;
		}
		if (file.size > maxBytes) {
			// `formatBytes`, not its own arithmetic: rounding to MB reported a 512 KiB cap as "1 MB",
			// contradicting the label on the box the file had just been dropped into. The helper for
			// that is defined below and was never wired in here.
			rejections.push(`${file.name}: exceeds ${formatBytes(maxBytes)}`);
			continue;
		}
		const key = fileKey(file);
		if (seen.has(key)) continue;
		seen.add(key);
		taken.push(file);
	}
	// Nothing usable arrived: keep the existing selection rather than clearing it, so a stray drop
	// of the wrong type cannot silently undo the files already chosen.
	if (taken.length === 0) return { files: current, rejections };
	return { files: multiple ? [...current, ...taken] : taken.slice(0, 1), rejections };
}

/** The selection with one file taken out. Out-of-range is returned unchanged — the list and the
 * click that removes from it can only disagree if something re-rendered in between. */
export function removeAt(files: File[], index: number): File[] {
	if (index < 0 || index >= files.length) return files;
	return [...files.slice(0, index), ...files.slice(index + 1)];
}
