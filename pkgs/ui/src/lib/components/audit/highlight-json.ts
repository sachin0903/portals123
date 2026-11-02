/**
 * A lightweight, SAFE JSON syntax highlighter: tokenise each line of a JSON string into
 * `{ text, cls }` segments the caller renders (and Svelte escapes) as plain text — NO `@html`, so a
 * captured string can never inject. Semantic tone tokens only (no `dark:` needed): keys `text-info`,
 * strings `text-success`, numbers `text-warning`, `true`/`false`/`null` muted.
 *
 * Shared by the Events detail pane (`event-detail.svelte`) and the audit-log detail pane, so the two
 * render the same "Details" block from one tokenizer rather than a drifting copy each.
 */
export interface HighlightSeg {
	text: string;
	cls: string;
}

export function highlightJson(src: string): HighlightSeg[][] {
	const re = /"(?:\\.|[^"\\])*"\s*:?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
	return src.split('\n').map((line) => {
		const segs: HighlightSeg[] = [];
		let lastIdx = 0;
		let m: RegExpExecArray | null;
		re.lastIndex = 0;
		while ((m = re.exec(line)) !== null) {
			if (m.index > lastIdx) segs.push({ text: line.slice(lastIdx, m.index), cls: '' });
			const tok = m[0];
			let cls: string;
			if (tok[0] === '"') cls = tok.trimEnd().endsWith(':') ? 'text-info' : 'text-success';
			else if (tok === 'true' || tok === 'false' || tok === 'null') cls = 'text-muted-foreground';
			else cls = 'text-warning';
			segs.push({ text: tok, cls });
			lastIdx = m.index + tok.length;
		}
		if (lastIdx < line.length) segs.push({ text: line.slice(lastIdx), cls: '' });
		return segs;
	});
}
