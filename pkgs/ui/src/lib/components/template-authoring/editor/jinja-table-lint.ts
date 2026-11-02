// Client-side balance check for a raw-Jinja data-table template (the editor's Raw mode). A live
// authoring nudge — NOT the authoritative parse (the server renders the real template) — so it only
// flags the unambiguous structural mistakes that make a table render blank or break the document:
// unbalanced `{{ }}` / `{% %}` delimiters and unclosed `{% for %}` / `{% if %}` blocks. Returns a
// list of human-readable warnings ([] when the template looks structurally sound).

const count = (tmpl: string, re: RegExp): number => (tmpl.match(re) || []).length;

export function lintJinjaTemplate(tmpl: string): string[] {
	const issues: string[] = [];
	if (!tmpl.trim()) return issues;

	const openOut = count(tmpl, /\{\{/g);
	const closeOut = count(tmpl, /\}\}/g);
	if (openOut !== closeOut) {
		issues.push(`Unbalanced output braces: ${openOut} “{{” vs ${closeOut} “}}”.`);
	}

	const openTag = count(tmpl, /\{%/g);
	const closeTag = count(tmpl, /%\}/g);
	if (openTag !== closeTag) {
		issues.push(`Unbalanced statement tags: ${openTag} “{%” vs ${closeTag} “%}”.`);
	}

	// `\bfor\b` after `{%` matches `{% for %}` but not `{% endfor %}` (that starts with "end").
	const fors = count(tmpl, /\{%-?\s*for\b/gi);
	const endfors = count(tmpl, /\{%-?\s*endfor\b/gi);
	if (fors !== endfors) {
		issues.push(`${fors} {% for %} vs ${endfors} {% endfor %} — a loop is not closed.`);
	}

	const ifs = count(tmpl, /\{%-?\s*if\b/gi);
	const endifs = count(tmpl, /\{%-?\s*endif\b/gi);
	if (ifs !== endifs) {
		issues.push(`${ifs} {% if %} vs ${endifs} {% endif %} — a condition is not closed.`);
	}

	return issues;
}
