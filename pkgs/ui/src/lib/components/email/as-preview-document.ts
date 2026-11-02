/**
 * Wrap a stored email fragment so the preview iframe is in standards mode.
 *
 * `srcdoc` of a bare table is quirks mode. The shell's card is
 * `width:100%;max-width:600px`, and in quirks that max-width is ignored — so a
 * frame that fills its pane stretches a 600px email across the monitor, which
 * is the squat preview. A doctype is preview chrome only; the stored body is
 * unchanged.
 */
export function asPreviewDocument(html: string): string {
	const start = html.trimStart();
	if (/^<!DOCTYPE/i.test(start) || /^<html[\s>]/i.test(start)) return html;
	return (
		'<!DOCTYPE html><html><head><meta charset="utf-8">' +
		'<meta name="viewport" content="width=device-width,initial-scale=1"></head>' +
		`<body style="margin:0">${html}</body></html>`
	);
}
