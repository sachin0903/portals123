/**
 * Renders the email chrome from `better-svelte-email` into a Python module the send path can use.
 *
 * WHY A BUILD STEP. The send path is Python (`apps/api/app/email/shell.py`) and the kit is a Svelte
 * renderer; the alternative is a Node process on the send path, which puts a second runtime between
 * a firm and its client's inbox. So the kit's markup is rendered ONCE, here, and checked in.
 *
 * WHY vite AND NOT plain node. `.svelte` needs the compiler — plain node answers
 * ERR_UNKNOWN_FILE_EXTENSION. `createServer().ssrLoadModule` is what compiles it, and vite is
 * already this package's build tool, so this needs no runner of its own.
 *
 * THE SEAM IS SENTINELS. Every value the send fills in is rendered as a sentinel string and rewritten
 * to a `%(name)s` placeholder, so the artefact is a template and the components hold no brand, copy
 * or colour. `%`-style, not `str.format`: a style attribute carries no braces today, but CSS is one
 * `@media` away from carrying them and a `.format()` on generated markup would then raise at send.
 *
 * Run: `moon run ui:build-email-chrome` (or `node scripts/build-email-chrome.mjs`).
 * A drift check runs it in CI and fails when the committed artefact is stale.
 */
import { createServer } from 'vite';
import { writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..');
const OUT = join(REPO, 'apps', 'api', 'app', 'email', 'chrome_generated.py');

/** The values the Python fills in, as `sentinel -> placeholder`. */
const SLOTS = {
	__ACCENT__: '%(accent)s',
	__CARD_BG__: '%(card)s',
	__FONT_STACK__: '%(font_stack)s',
	__INK__: '%(ink)s',
	__BODY_INK__: '%(body_ink)s',
	__CARD_WIDTH__: '%(card_width)s',
	__ACCENT_BAR__: '%(accent_bar)s',
	__WORDMARK__: '%(wordmark)s',
	__HEADING__: '%(heading)s',
	__BODY__: '%(body_html)s',
	__CTA__: '%(cta)s',
	__FOOTER__: '%(footer)s',
	__LABEL__: '%(label)s',
	__URL__: '%(url)s',
	__CELL_STYLE__: '%(cell_style)s',
	__LINK_STYLE__: '%(link_style)s',
	__LABEL_STYLE__: '%(label_style)s',
	__CTA_CLASS__: '%(cta_class)s',
	__INNER__: '%(inner)s'
};

/** Svelte's SSR anchors. Narrow ON PURPOSE: an `<!--[if mso]>` conditional must survive. */
const stripSvelteComments = (html) =>
	html.replace(/<!--\[-->|<!--\]-->|<!---->|<!--[0-9a-z]{4,}-->/g, '');

/** The kit's `Renderer` wraps a fragment in a whole document; `document.py` owns that. */
const unwrapDocument = (html) => {
	const open = html.indexOf('<body>');
	const close = html.lastIndexOf('</body>');
	if (open === -1 || close === -1) throw new Error('kit output carried no <body> to unwrap');
	return html.slice(open + '<body>'.length, close);
};

/**
 * WHITESPACE BETWEEN TAGS IS FATAL IN THE EDITOR, so it is removed here rather than in the
 * components. GrapesJS promotes an element whose children include a raw textnode to a single `text`
 * component (`email-editor/create.ts`, markup rule 2), so one space between the accent-bar table and
 * the content table collapses the whole card into one RTE field and it stops being a droppable
 * structure. Svelte's SSR COLLAPSES inter-tag whitespace to a single space rather than dropping it,
 * so no compiler option and no amount of tidy indentation in the components can avoid this.
 *
 * Safe because it only matches whitespace that is the ENTIRE content between two tags, and only the
 * CHROME is generated: an author's own markup arrives through a `%(slot)s` at send time and is never
 * touched. Send-shape-only would not do — the editor shape is the one that has to survive parsing.
 */
// REGEX LITERALS, never `new RegExp` with a template literal: `\s` inside a template literal
// collapses to a bare `s`, so the pattern silently matched the letter instead of whitespace and the
// strip below did nothing at all. It read as working — the artefact regenerated, the drift check
// passed, and the whitespace was still there.
const stripInterTagWhitespace = (html) =>
	html
		.replace(/>\s+</g, '><')
		// AND EITHER SIDE OF A SLOT, which the pattern above cannot see: the character before the space
		// is the sentinel, not `>`. At send the slot is filled with markup ending in `>`, so
		// `%(wordmark)s <h1` becomes exactly the textnode-among-children case — the same defect one
		// substitution later.
		.replace(/>\s+(__[A-Z_]+__)/g, '>$1')
		.replace(/(__[A-Z_]+__)\s+</g, '$1<')
		.replace(/(__[A-Z_]+__)\s+(__[A-Z_]+__)/g, '$1$2');

const toTemplate = (html) => {
	let out = stripInterTagWhitespace(unwrapDocument(stripSvelteComments(html)));
	// A literal `%` in the markup would break `%`-formatting, so double it FIRST — before the
	// placeholders are introduced, or their own `%` would be doubled too.
	out = out.replace(/%/g, '%%');
	for (const [sentinel, placeholder] of Object.entries(SLOTS)) {
		out = out.split(sentinel).join(placeholder);
	}
	const leftover = out.match(/__[A-Z_]+__/g);
	if (leftover) throw new Error(`unrewritten sentinel(s): ${[...new Set(leftover)].join(', ')}`);
	return out;
};

/**
 * The markup as a Python string literal in the style `ruff format` would choose, so the generated
 * module passes the repo's own `:format` gate. `JSON.stringify` always double-quotes and escapes the
 * inner quotes; ruff rewrites that to a single-quoted literal, because the markup is full of `"`
 * attributes — so the artefact was committed in a shape CI would reformat.
 *
 * The rule is Black's, which ruff follows: prefer double quotes, but switch to single when that
 * removes the escaping. Both present keeps double. A newline would need a different literal form
 * altogether, and the chrome is minified, so it is asserted rather than handled.
 */
const NEWLINE = new RegExp('[\\r\\n]');
const BACKSLASH = new RegExp('\\\\', 'g');
const DQUOTE = new RegExp('"', 'g');

const pyLiteral = (s) => {
	if (NEWLINE.test(s)) throw new Error('chrome markup carried a newline');
	const escaped = s.replace(BACKSLASH, '\\\\');
	return s.includes('"') && !s.includes("'")
		? `'${escaped}'`
		: `"${escaped.replace(DQUOTE, '\\"')}"`;
};

const server = await createServer({
	server: { middlewareMode: true },
	appType: 'custom',
	logLevel: 'error'
});

try {
	const { Renderer } = await server.ssrLoadModule('@better-svelte-email/server');
	const chrome = await server.ssrLoadModule('/src/lib/email-chrome/chrome.svelte');
	const cta = await server.ssrLoadModule('/src/lib/email-chrome/chrome-cta.svelte');
	const footer = await server.ssrLoadModule('/src/lib/email-chrome/chrome-footer.svelte');
	const renderer = new Renderer({ disableTailwind: true });

	const shellProps = {
		card: '__CARD_BG__',
		fontStack: '__FONT_STACK__',
		ink: '__INK__',
		bodyInk: '__BODY_INK__',
		cardWidth: '__CARD_WIDTH__',
		accentBarStyle: '__ACCENT_BAR__',
		wordmark: '__WORDMARK__',
		heading: '__HEADING__',
		body: '__BODY__',
		cta: '__CTA__',
		footer: '__FOOTER__'
	};
	const ctaProps = {
		label: '__LABEL__',
		url: '__URL__',
		cellStyle: '__CELL_STYLE__',
		linkStyle: '__LINK_STYLE__',
		labelStyle: '__LABEL_STYLE__',
		ctaClass: '__CTA_CLASS__'
	};

	const footerProps = { cellStyle: '__CELL_STYLE__', inner: '__INNER__' };

	const shapes = {};
	for (const [key, mod, props] of [
		['SHELL', chrome, shellProps],
		['CTA', cta, ctaProps],
		['FOOTER', footer, footerProps]
	]) {
		for (const named of [false, true]) {
			const html = await renderer.render(mod.default, { props: { ...props, names: named } });
			shapes[`${key}${named ? '_NAMED' : ''}`] = toTemplate(html);
		}
	}

	const py = `"""GENERATED — DO NOT EDIT. Run \`moon run ui:build-email-chrome\`.

The email chrome as \`better-svelte-email\` renders it, from
\`pkgs/ui/src/lib/email-chrome/chrome.svelte\` and \`chrome-cta.svelte\`. Those two files carry the
reasoning; this one carries only their output, with every send-time value as a \`%(name)s\`
placeholder.

Two shapes each: the SEND shape, and the \`_NAMED\` editor shape carrying \`data-gjs-name\` so an
author's layer tree reads in the palette's own words. They differ by nothing else — the same
invariant \`defaults.py::_both\` keeps for the pair it builds.
"""

from __future__ import annotations

SHELL = ${pyLiteral(shapes.SHELL)}

SHELL_NAMED = ${pyLiteral(shapes.SHELL_NAMED)}

CTA = ${pyLiteral(shapes.CTA)}

CTA_NAMED = ${pyLiteral(shapes.CTA_NAMED)}

FOOTER = ${pyLiteral(shapes.FOOTER)}

FOOTER_NAMED = ${pyLiteral(shapes.FOOTER_NAMED)}
`;
	const before = (() => {
		try {
			return readFileSync(OUT, 'utf8');
		} catch {
			return null;
		}
	})();
	// LINE ENDINGS NORMALISED BEFORE COMPARING. Git's `core.autocrlf` rewrites this file to CRLF on
	// checkout while the generator emits LF, so a byte comparison calls it stale after any checkout,
	// branch switch or rebase — it did exactly that after rebasing onto main, with the content
	// identical. The check is about the CONTENT drifting from the components, not about which
	// newline the working copy happens to hold.
	const lf = (s) => (s === null ? null : s.replace(/\r\n/g, '\n'));
	if (process.argv.includes('--check')) {
		if (lf(before) !== lf(py)) {
			console.error(
				'chrome_generated.py is STALE — run `moon run ui:build-email-chrome` and commit the result.'
			);
			process.exit(1);
		}
		console.log('chrome_generated.py is up to date.');
	} else {
		writeFileSync(OUT, py, 'utf8');
		console.log(`wrote ${OUT}`);
		for (const [k, v] of Object.entries(shapes)) {
			console.log(`  ${k}: ${v.length} chars, ${(v.match(/<table/g) || []).length} tables`);
		}
	}
} finally {
	await server.close();
}
