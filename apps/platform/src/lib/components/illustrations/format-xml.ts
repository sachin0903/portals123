/**
 * Indent a CTC1 message for reading.
 *
 * The API returns the XML exactly as it is sent — one line, no whitespace — because that is the
 * artefact, and the builder's output is byte-for-byte asserted in `test_ctc_xml_builder.py`. This
 * reformats a COPY for display only: an adviser looking at what went to the engine should not have
 * to scroll a 1,300-character line.
 *
 * Deliberately not a parser. It splits on tag boundaries and tracks depth, which is enough for the
 * builder's output (elements and text, no comments, no CDATA) and cannot throw on input it doesn't
 * understand — the worst case is a line indented oddly.
 */
export function formatXml(xml: string): string {
	const lines = xml.trim().replace(/>\s*</g, '>\n<').split('\n');
	let depth = 0;
	return lines
		.map((raw) => {
			const line = raw.trim();
			if (line === '') return '';
			const closing = /^<\//.test(line);
			// An opening tag with nothing after it: a container, so everything below it indents.
			const opening = /^<[^!?/]/.test(line) && !/\/>$/.test(line) && !line.includes('</');
			if (closing) depth = Math.max(0, depth - 1);
			const out = '  '.repeat(depth) + line;
			if (opening) depth += 1;
			return out;
		})
		.join('\n');
}
