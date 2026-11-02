/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Load stored template content back into the editor. Prefers the GrapesJS
 * project blob (`gjs_project`); falls back to HTML/CSS wrapped in .page divs.
 * Ported from the reference.
 */

/** Load HTML-only content as a single document, ensuring .page wrappers. */
export function loadHtmlIntoEditor(editor: any, html: string, css?: string): void {
	const sections = html.split(
		/<div[^>]*style="[^"]*page-break-after\s*:\s*always[^"]*"[^>]*>\s*<\/div>/gi
	);
	const hasPageWrapper = /<div[^>]*class="[^"]*page[^"]*"/.test(html);

	if (sections.length > 1) {
		editor.setComponents(sections.map((s) => `<div class="page">${s}</div>`).join('\n'));
	} else if (hasPageWrapper) {
		editor.setComponents(html);
	} else {
		editor.setComponents(`<div class="page">${html}</div>`);
	}
	if (css) editor.setStyle(css);
}

/** Load either a gjs_project blob or html/css. Empty → a single blank page. */
export function loadInto(
	editor: any,
	data: { gjs_project?: unknown; html?: string; css?: string } | null
): void {
	if (data?.gjs_project) {
		try {
			editor.loadProjectData(data.gjs_project);
			return;
		} catch {
			/* fall through to html */
		}
	}
	if (data?.html) {
		loadHtmlIntoEditor(editor, data.html, data.css);
		return;
	}
	editor.setComponents('<div class="page"></div>');
}
