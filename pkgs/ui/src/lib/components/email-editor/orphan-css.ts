/** Drop the `#id` rules `getCss()` re-emits from inline declarations — 24 of them, 1557 bytes on the
 *  shipped document-request template, measured. None can apply (`avoidInlineStyle: false` puts styles ON
 *  the element; `getHtml()` carries no `id`), they regenerate on every parse so an untouched template
 *  stores a phantom diff on every save, and they bury the rule that does matter: one that can only exist
 *  AS a rule — an at-rule, a pseudo-class — renders in the canvas and nowhere else (`keep_style_tags`). */

/** `#id` selectors GrapesJS emits, with their rule bodies. THE BODY CANNOT BE `[^}]*`: a declaration
 *  VALUE may hold a Jinja token (`background-color:{{ brand.accent }}`) whose `}}` ends the body early,
 *  leaving the tail behind as broken CSS. So the body is a whole `{{ … }}` token or a non-brace char. */
const ID_RULE = /#([\w-]+)((?:\s*,\s*#[\w-]+)*)\s*\{(?:\{\{[\s\S]*?\}\}|[^{}])*\}/g;

/** `css` with every rule whose selectors are ALL unmatchable ids removed — pruned by matching, so a
 *  hand-set `#id` rule survives, and a multi-selector rule survives if any one selector can match. */
export function pruneOrphanCss(css: string, html: string): string {
	if (!css) return '';
	// `?? ''` rather than a non-null assertion: the empty string is already the "no ids here" answer.
	const present = new Set(
		[...html.matchAll(/\sid="([^"]*)"/g)].flatMap((m) => (m[1] ?? '').split(/\s+/).filter(Boolean))
	);
	const out = css.replace(ID_RULE, (rule: string, first: string, rest: string) => {
		const ids = [first, ...(rest ?? '').split(',').map((p) => p.trim().replace(/^#/, ''))].filter(
			Boolean
		);
		return ids.some((id) => present.has(id)) ? rule : '';
	});
	// Nothing but orphans leaves whitespace; normalised so the value cannot read as "there is css here".
	return out.trim() === '' ? '' : out;
}
