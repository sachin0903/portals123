// GrapesJS article canvas — browser-only. It deliberately has none of GrapesJS's stock panels:
// their unlabeled icon rail is an implementation console, not an authoring surface. The Svelte
// shell owns the small, named command set and GrapesJS owns only the editable page.

export const STARTER_HTML = `<div class="article-shell">
<p class="article-kicker">Insight</p>
<h1>Give your article a clear headline</h1>
<p class="article-lede">Open with one or two sentences that tell the reader why this matters.</p>
<h2>Add a useful subheading</h2>
<p>Write in short paragraphs. Use the toolbar to add lists, pull quotes and callouts.</p>
</div>`;

export const DEFAULT_ARTICLE_CSS = `
.article-shell {
  box-sizing: border-box;
  max-width: 760px;
  min-height: 720px;
  margin: 0 auto;
  padding: 64px 72px 88px;
  background: var(--background);
  color: var(--foreground);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 17px;
  line-height: 1.75;
}
.article-shell * { box-sizing: border-box; }
.article-shell .article-kicker {
  margin: 0 0 18px;
  color: var(--primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .14em;
  line-height: 1.4;
  text-transform: uppercase;
}
.article-shell h1 {
  max-width: 680px;
  margin: 0 0 24px;
  color: var(--foreground);
  font-size: 48px;
  font-weight: 650;
  letter-spacing: -.035em;
  line-height: 1.08;
}
.article-shell .article-lede {
  margin: 0 0 44px;
  color: var(--muted-foreground);
  font-size: 21px;
  line-height: 1.55;
}
.article-shell h2 {
  margin: 44px 0 14px;
  color: var(--foreground);
  font-size: 29px;
  font-weight: 650;
  letter-spacing: -.02em;
  line-height: 1.2;
}
.article-shell h3 {
  margin: 32px 0 10px;
  color: var(--foreground);
  font-size: 21px;
  font-weight: 650;
  line-height: 1.3;
}
.article-shell p { margin: 0 0 20px; }
.article-shell ul, .article-shell ol { margin: 8px 0 26px; padding-left: 26px; }
.article-shell li { margin: 7px 0; padding-left: 5px; }
.article-shell blockquote {
  margin: 36px 0;
  padding: 6px 0 6px 24px;
  border-left: 3px solid var(--primary);
  color: var(--foreground);
  font-size: 23px;
  font-weight: 520;
  line-height: 1.5;
}
.article-shell .article-callout {
  margin: 32px 0;
  padding: 22px 24px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--accent);
  color: var(--accent-foreground);
}
.article-shell hr { margin: 44px 0; border: 0; border-top: 1px solid var(--border); }
.article-shell img {
  display: block;
  /* auto deliberately, NOT 100%: a Word document's images are mostly small graphics and icons, and
     stretching one of those to the full 616px column is much of what made an imported document read
     as broken. A genuine photograph is wider than the column and still fills it. */
  width: auto;
  max-width: 100%;
  height: auto;
  margin: 36px auto;
  border-radius: var(--radius);
}
.article-shell table {
  width: 100%;
  margin: 32px 0;
  border-collapse: collapse;
  font-size: 15px;
}
.article-shell th, .article-shell td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}
.article-shell th { color: var(--foreground); font-weight: 650; }
@media (max-width: 640px) {
  .article-shell { min-height: 0; padding: 24px 16px 40px; font-size: 16px; }
  .article-shell h1 { font-size: 32px; }
  .article-shell .article-lede { margin-bottom: 28px; font-size: 18px; }
  .article-shell h2 { margin-top: 32px; font-size: 24px; }
}`;

export type ArticleBlock = 'heading' | 'text' | 'list' | 'quote' | 'callout' | 'divider';
export type ArticleDevice = 'desktop' | 'tablet' | 'mobile';

/**
 * The canvas height, and it MUST be an absolute length — never a percentage.
 *
 * GrapesJS writes `config.height` as an INLINE style on the container it is given, so `'100%'`
 * resolves against whatever the app happens to have wrapped the editor in. Ours is a padded box with
 * a `min-height`, i.e. no definite height, so the percentage resolved to `auto`, and the only thing
 * inside is an absolutely-positioned canvas that contributes no height — every box in the editor
 * came out ZERO TALL and the page vanished, with the article itself correctly laid out at 760x720
 * inside an iframe nobody could see. It is not a hypothetical: it shipped, and loading GrapesJS's
 * core stylesheet is what turned it from "wrong but visible" into "blank", because without that
 * sheet the canvas is statically positioned and stumbles into a height.
 */
export const CANVAS_HEIGHT = '42rem';

/**
 * Editor chrome only — injected into the HOST document and never part of `getCss()`, so none of it
 * can reach a published article.
 *
 * The `<link>` is the load-bearing half. GrapesJS's core stylesheet is what sizes its badges and its
 * component toolbar; without it the toolbar's SVGs render at their intrinsic size (huge black
 * select-parent and move arrows over the page) and the canvas — absolutely positioned, sized off
 * `--gjs-*-width` — collapses into a narrow clipped column. It cannot be `import`ed: LightningCSS
 * rejects GrapesJS's IE `filter` syntax, which is why the email editor serves the same file as a
 * static asset. Same element id, so the two editors share one link.
 *
 * Everything else is scoped to `.article-gjs` so it cannot reach the email editor's canvas, and is
 * written in design tokens rather than literals: GrapesJS's default chrome blue would otherwise be
 * the one colour in the product that no firm's branding can move.
 */
function ensureArticleEditorStyles(): void {
	if (!document.getElementById('gjs-css')) {
		const link = document.createElement('link');
		link.id = 'gjs-css';
		link.rel = 'stylesheet';
		link.href = '/grapesjs.css';
		document.head.appendChild(link);
	}
	if (document.getElementById('gjs-article-layout')) return;
	const style = document.createElement('style');
	style.id = 'gjs-article-layout';
	style.textContent = [
		// No panels are registered, so the canvas takes the whole host. GrapesJS reserves room for
		// its own rails through these variables; left standing they leave the page off-centre.
		'.article-gjs .gjs-editor{--gjs-left-width:0px;--gjs-canvas-top:0px;--gjs-right-width:0px;background:transparent!important;font-family:inherit!important}',
		'.article-gjs .gjs-cv-canvas{inset:0!important;width:100%!important;height:100%!important;padding:0!important;background:transparent!important}',
		'.article-gjs .gjs-frame-wrapper__top{display:none!important}',
		// The sheet's own surround is the parent's backdrop; the iframe itself must not paint one.
		'.article-gjs .gjs-frame{background:transparent!important}',
		// Selection affordances in brand tokens, and readable at article scale.
		'.article-gjs .gjs-badge,.article-gjs .gjs-toolbar{background:var(--brand)!important;color:var(--primary-foreground)!important;border-radius:calc(var(--radius) / 2)!important;font-family:inherit!important;font-size:11px!important}',
		'.article-gjs .gjs-toolbar-item{padding:4px!important}',
		'.article-gjs .gjs-toolbar-item svg{width:14px!important;height:14px!important;display:block!important}',
		'.article-gjs .gjs-highlighter,.article-gjs .gjs-highlighter-sel{outline-color:var(--brand)!important}',
		'.article-gjs .gjs-resizer-h{border-color:var(--brand)!important}'
	].join('');
	document.head.appendChild(style);
}

export async function createArticleEditor(
	container: HTMLElement,
	html: string,
	onUpload?: (file: File) => Promise<string>
): Promise<{
	getHtml: () => string;
	getCss: () => string;
	destroy: () => void;
	onChange: (fn: () => void) => void;
	setContent: (nextHtml: string) => void;
	addBlock: (block: ArticleBlock) => void;
	addImage: (url: string, alt?: string) => void;
	undo: () => void;
	redo: () => void;
	setDevice: (device: ArticleDevice) => void;
}> {
	ensureArticleEditorStyles();
	const grapesjs = (await import('grapesjs')).default;
	const editor = grapesjs.init({
		container,
		height: CANVAS_HEIGHT,
		fromElement: false,
		storageManager: false,
		noticeOnUnload: false,
		panels: { defaults: [] },
		canvas: {
			styles: [],
			scripts: []
		},
		// THE ARTICLE STYLESHEET GOES IN VERBATIM, AND NEVER THROUGH `editor.setStyle`.
		//
		// GrapesJS re-serialises a parsed rule through the CSSOM, which expands shorthands into
		// longhands — and a shorthand whose value contains `var()` CANNOT be expanded, so it is
		// dropped outright and silently. `background: var(--background)` computed to transparent and
		// `border-left: 3px solid var(--primary)` to a 0px border: the sheet had no background, pull
		// quotes had no rule and callouts no border, in the editor AND in the published article,
		// because publish inlines the same mangled CSS.
		//
		// Injecting it as `canvasCss` also keeps it out of `getCss()`, which is right: this is the
		// design system's editorial stylesheet, not per-item author data. Nobody can author CSS here
		// (`styleManager.sectors` is empty), so there is nothing to round-trip, and `getCss()` below
		// reports this constant instead.
		canvasCss: [
			'*{box-sizing:border-box}',
			'html,body{margin:0;background:transparent}',
			DEFAULT_ARTICLE_CSS,
			// Canvas-only chrome: the sheet's lift off the backdrop, which must NOT follow the
			// article into a client portal.
			'.article-shell{box-shadow:0 18px 55px color-mix(in oklch, var(--foreground) 16%, transparent)}',
			// A component GrapesJS is hovering must not shift the layout it is measuring.
			'[data-gjs-highlightable]{outline-offset:-1px}'
		].join(''),
		deviceManager: {
			devices: [
				{ id: 'desktop', name: 'Desktop', width: '' },
				{ id: 'tablet', name: 'Tablet', width: '768px', widthMedia: '900px' },
				{ id: 'mobile', name: 'Mobile', width: '390px', widthMedia: '480px' }
			]
		},
		assetManager: onUpload
			? {
					upload: ' ',
					autoAdd: true,
					uploadFile: async (ev: Event) => {
						const input = ev.target as HTMLInputElement | null;
						const transfer = (ev as DragEvent).dataTransfer;
						const file = transfer?.files?.[0] ?? input?.files?.[0];
						if (!file) return;
						const url = await onUpload(file);
						editor.AssetManager.add(url);
					}
				}
			: { upload: false },
		styleManager: { sectors: [] },
		selectorManager: { componentFirst: true }
	});
	editor.setComponents(html.trim() ? html : STARTER_HTML);
	editor.on('load', () => {
		// GrapesJS edits in an iframe, so app-level custom properties do not inherit naturally.
		// Copy only the semantic tokens this article stylesheet consumes. The saved CSS still names
		// the variables, so the client's own tenant theme resolves them when the article is read.
		const frameRoot = editor.Canvas.getFrameEl()?.contentDocument?.documentElement;
		if (!frameRoot) return;
		const appStyle = getComputedStyle(document.documentElement);
		for (const token of [
			'--background',
			'--foreground',
			'--primary',
			'--muted-foreground',
			'--border',
			'--accent',
			'--accent-foreground',
			'--radius'
		]) {
			frameRoot.style.setProperty(token, appStyle.getPropertyValue(token));
		}
	});

	const blockHtml: Record<ArticleBlock, string> = {
		heading: '<h2>A useful subheading</h2>',
		text: '<p>Write a short, focused paragraph here.</p>',
		list: '<ul><li>First useful point</li><li>Second useful point</li></ul>',
		quote: '<blockquote>Bring an important idea or expert voice to the foreground.</blockquote>',
		callout:
			'<div class="article-callout"><strong>Good to know</strong><p>Add the practical detail your reader should not miss.</p></div>',
		divider: '<hr>'
	};

	return {
		getHtml: () => editor.getHtml() ?? '',
		// The constant, not `editor.getCss()`: see the `canvasCss` note. An item saved before this
		// carries the mangled copy, and reporting the constant is what corrects it on the next save.
		getCss: () => DEFAULT_ARTICLE_CSS,
		destroy: () => editor.destroy(),
		onChange: (fn) => {
			editor.on('update', fn);
		},
		setContent: (nextHtml) => {
			editor.setComponents(nextHtml);
		},
		addBlock: (block) => {
			editor.addComponents(blockHtml[block]);
		},
		addImage: (url, alt = '') => {
			editor.addComponents({
				type: 'image',
				attributes: { src: url, alt, loading: 'lazy' }
			});
		},
		undo: () => editor.UndoManager.undo(),
		redo: () => editor.UndoManager.redo(),
		setDevice: (device) => editor.setDevice(device)
	};
}
