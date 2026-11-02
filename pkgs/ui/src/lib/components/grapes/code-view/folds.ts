// Folding reads INDENTATION, not the tag tree: `formatHtml`'s indent already IS the tag tree, so this
// keeps working on hand-edited text and fails safe (a short range hides too few lines, never too many).

/** A foldable region. Both are 0-based line indices; `head` stays visible, `head+1 … end` hide. */
export interface FoldRange {
	head: number;
	end: number;
}

/** Leading spaces; a tab counts as one — `formatHtml` only emits spaces, a hand-edited tab just needs to be self-consistent. */
export function indentOf(line: string): number {
	const match = /^[ \t]*/.exec(line);
	return match ? match[0].length : 0;
}

/** True for a blank line — skipped when measuring a region, so a blank line inside a block doesn't end the fold. */
function blank(line: string | undefined): boolean {
	return !line || line.trim() === '';
}

// A line is a head when the next non-blank line is indented FURTHER; the region runs to the last line still indented further. One range per head, so nesting produces nested ranges.
export function foldRanges(lines: readonly string[]): FoldRange[] {
	const ranges: FoldRange[] = [];
	for (let i = 0; i < lines.length; i += 1) {
		const line = lines[i];
		if (blank(line)) continue;
		const depth = indentOf(line as string);
		let j = i + 1;
		while (j < lines.length && blank(lines[j])) j += 1;
		if (j >= lines.length || indentOf(lines[j] as string) <= depth) continue;
		let end = j;
		for (let k = j; k < lines.length; k += 1) {
			if (blank(lines[k])) continue;
			if (indentOf(lines[k] as string) <= depth) break;
			end = k;
		}
		ranges.push({ head: i, end });
	}
	return ranges;
}

// A nested collapsed range is a subset of the range around it, so a plain union is the whole answer —
// no containment logic needed, and a head whose own line is hidden contributes nothing new.
export function hiddenLines(
	ranges: readonly FoldRange[],
	collapsed: ReadonlySet<number>
): Set<number> {
	const hidden = new Set<number>();
	for (const range of ranges) {
		if (!collapsed.has(range.head)) continue;
		for (let i = range.head + 1; i <= range.end; i += 1) hidden.add(i);
	}
	return hidden;
}

/** The range headed by `line`, or null — what the gutter's chevron is drawn from. */
export function rangeAt(ranges: readonly FoldRange[], line: number): FoldRange | null {
	return ranges.find((range) => range.head === line) ?? null;
}
