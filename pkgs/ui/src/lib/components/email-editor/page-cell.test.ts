import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import {
	BRAND_ARM_ATTR,
	BRAND_ARM_LOGO,
	BRAND_LOGO_HTML,
	ensurePageCell,
	isCanvasEmpty,
	isTokenSource,
	registerEmailImage,
	registerImageActivation,
	registerMastheadImage,
	PAGE_ATTR,
	PAGE_HTML,
	PAGE_INSET,
	PAGE_MIN_HEIGHT_PX,
	pageCell,
	STARTER_HTML
} from './create';

/** THE PAGE IS A CELL. A block could only be dropped into a `<td>` that already existed, and the refusal is
 *  inside GrapesJS's own target resolution — upstream of anything observable — so this removes the case. */
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const canvasCss = readFileSync(
	join(REPO_ROOT, 'apps', 'platform', 'static', 'gjs-email-canvas.css'),
	'utf8'
);

interface Fake {
	attrs: Record<string, string>;
	children: Fake[];
	moved: { to: Fake; at: number } | null;
	/** What `protectPage` set. Recorded on the NODE: `model()` builds a fresh object per call, so a spy there would be invisible. */
	props: Record<string, unknown>;
}

function node(attrs: Record<string, string> = {}, children: Fake[] = []): Fake {
	return { attrs, children, moved: null, props: {} };
}

function fakeEditor(children: Fake[]) {
	const appended: string[] = [];
	const cell = node({ [PAGE_ATTR]: 'true' });
	const page = node({ [PAGE_ATTR]: 'true' }, [node({}, [node({}, [cell])])]);
	const model = (n: Fake): Record<string, unknown> => ({
		getAttributes: () => n.attrs,
		get: (key: string) => (key === 'tagName' ? (n === cell ? 'td' : 'table') : undefined),
		// `map`, because `childList` reads the collection that way, not via `.models` — a `models`-only fake made the wrap look non-idempotent.
		components: () => {
			const kids = n.children.map(model);
			return { map: <T>(fn: (c: unknown) => T) => kids.map(fn), length: kids.length };
		},
		move: vi.fn((to: unknown, opts: { at: number }) => {
			skipped.push(inSkip);
			n.moved = { to: to as Fake, at: opts.at };
		}),
		set: vi.fn((patch: Record<string, unknown>) => {
			skipped.push(inSkip);
			Object.assign(n.props, patch);
		}),
		remove: vi.fn()
	});
	const wrapperChildren = [...children];
	// The undo pause is RECORDED, not stubbed: a fake that swallowed the call could not tell "wrapped inside skip" from "skip was called".
	const skipped: boolean[] = [];
	let inSkip = false;
	return {
		appended,
		cell,
		page,
		skipped,
		allWorkWasSkipped: () => skipped.length > 0 && skipped.every(Boolean),
		editor: {
			UndoManager: {
				skip: (fn: () => void) => {
					inSkip = true;
					try {
						fn();
					} finally {
						inSkip = false;
					}
				}
			},
			getWrapper: () => ({
				components: () => {
					const kids = wrapperChildren.map(model);
					return { map: <T>(fn: (c: unknown) => T) => kids.map(fn), length: kids.length };
				},
				append: vi.fn((html: string) => {
					appended.push(html);
					wrapperChildren.unshift(page);
					return [model(page)];
				})
			})
		}
	};
}

/** Source minus its comments, for an assertion about what the code DOES: the canvas rule's comment quotes the `padding: … !important` it no longer sets. */
function withoutComments(text: string): string {
	return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

describe('PAGE_HTML', () => {
	it('is a full-width one-cell container table', () => {
		expect(PAGE_HTML).toContain('<table');
		expect(PAGE_HTML).toContain('<td');
		expect(PAGE_HTML).toContain('width:100%');
		// The attributes Outlook's Word engine reads; without them it applies its own cell spacing.
		expect(PAGE_HTML).toContain('cellpadding="0"');
		expect(PAGE_HTML).toContain('cellspacing="0"');
		expect(PAGE_HTML).toContain('role="presentation"');
	});

	it('top-aligns its content', () => {
		// A cell taller than its content centres it, so a half-empty email would float in the middle of the card.
		expect(PAGE_HTML).toContain('vertical-align:top');
	});

	it('marks itself, on both the table and the cell', () => {
		// A NAME could not do this job: an author can rename anything, and the check has to survive that.
		expect(PAGE_HTML.match(new RegExp(`${PAGE_ATTR}="true"`, 'g'))).toHaveLength(2);
	});
});

describe('ensurePageCell', () => {
	it('wraps existing content and keeps its order', () => {
		const first = node({ id: 'a' });
		const second = node({ id: 'b' });
		const { editor, appended, cell } = fakeEditor([first, second]);
		ensurePageCell(editor);
		expect(appended).toEqual([PAGE_HTML]);
		// In order, and by MOVE — re-parsing would give the components new ids and lose their styles.
		expect(first.moved?.at).toBe(0);
		expect(second.moved?.at).toBe(1);
		expect(cell).toBeTruthy();
	});

	it('is IDEMPOTENT — the one thing that must not go wrong', () => {
		// It runs on every load, so a nested second page table would halve the usable width each time.
		const already = node({ [PAGE_ATTR]: 'true' });
		const { editor, appended } = fakeEditor([already]);
		ensurePageCell(editor);
		expect(appended).toEqual([]);
	});

	it('wraps a blank canvas too', () => {
		const { editor, appended } = fakeEditor([]);
		ensurePageCell(editor);
		expect(appended).toEqual([PAGE_HTML]);
	});

	it('does nothing without a wrapper', () => {
		expect(() => ensurePageCell({})).not.toThrow();
		expect(() => ensurePageCell(undefined)).not.toThrow();
	});
});

describe('the page is chrome, not content', () => {
	it('cannot be dragged, copied or deleted', () => {
		const { editor, page, cell } = fakeEditor([node()]);
		ensurePageCell(editor);
		for (const part of [page, cell]) {
			expect(part.props.draggable).toBe(false);
			expect(part.props.copyable).toBe(false);
			expect(part.props.removable).toBe(false);
			expect(part.props.toolbar).toEqual([]);
		}
	});

	it('makes the page UNSELECTABLE all the way down, and still droppable', () => {
		// The table constructor inserts an unnamed `<tbody>`/`<tr>`, so clicking the page selected a "Row". Nothing in
		// the page is selectable, the cell included: the Page style panel only shows while the page is NOT selected.
		const { editor, page, cell } = fakeEditor([node()]);
		const tbody = page.children[0]!;
		const row = tbody.children[0]!;
		ensurePageCell(editor);
		expect(cell.props.droppable).toBe(true);
		// Named on the MODEL: GrapesJS's parser consumes `data-gjs-*`, so a reloaded page would badge as "Cell".
		expect(cell.props.name).toBe('Page');
		for (const part of [page, tbody, row, cell]) {
			expect(part.props.selectable).toBe(false);
			expect(part.props.hoverable).toBe(false);
		}
	});

	it('never touches what is inside the page', () => {
		// It stops at the first `<td>`: below that is the author's content, and locking it would make the email uneditable.
		const content = node();
		const { editor, cell } = fakeEditor([content]);
		ensurePageCell(editor);
		expect(content.props).toEqual({});
		expect(cell.children.length).toBe(0);
	});

	it('carries its height and its inset as REAL styles, not canvas fictions', () => {
		// Both were canvas-only at first, so the cell measured 600px with a 32px inset here and `padding:0` in the sent
		// mail. `height` on a `<td>` is a floor and travels; Outlook's Word engine ignores `min-height`.
		expect(PAGE_HTML).toContain(`height:${PAGE_MIN_HEIGHT_PX}px`);
		expect(PAGE_HTML).toContain(`padding:${PAGE_INSET}`);
		expect(PAGE_HTML).not.toContain('padding:0');
	});

	it('is faked nowhere — the canvas sheet states neither of them', () => {
		// Either value declared canvas-only again shows one thing and sends another. Comments stripped, per `withoutComments`.
		const css = withoutComments(canvasCss);
		expect(css).not.toMatch(/td\[data-email-page\]\s*\{[^}]*padding:/);
		expect(css).not.toMatch(/\[data-email-page\]\s*\{[^}]*height:/);
		expect(css).not.toContain('32px 32px 36px');
	});
});

describe('the page fills the sheet', () => {
	it('needs no JS floor, because the height is in the markup', () => {
		const create = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');
		const sync =
			create.split('export function syncFrameHeight')[1]?.split('export function')[0] ?? '';
		const code = withoutComments(sync);
		expect(create).not.toContain('PAGE_FILL_STYLE_ID');
		expect(code).not.toContain('min-height');
		expect(code).not.toMatch(/min-height:\$\{/);
	});

	it('still floors the FRAME, and from the same number as the page', () => {
		// The iframe must be at least as tall as the page, and it is ONE number: `MIN_FRAME_HEIGHT` derives from it.
		const create = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');
		expect(create).toContain('export const MIN_FRAME_HEIGHT = PAGE_MIN_HEIGHT_PX;');
		const sync =
			create.split('export function syncFrameHeight')[1]?.split('export function')[0] ?? '';
		expect(withoutComments(sync)).toContain('MIN_FRAME_HEIGHT');
	});

	it('stands the inset down for a shipped default, and leaves it alone for the starter', () => {
		// A SHIPPED DEFAULT IS A WHOLE SHELL, so `loadDefault` stands the page inset down; the starter is plain content.
		const editor = readFileSync(join(import.meta.dirname, 'email-editor.svelte'), 'utf8');
		const starter = editor.split('function insertStarter')[1]?.split('\n\tfunction ')[0] ?? '';
		const loadDefault = editor.split('function loadDefault')[1]?.split('\n\tfunction ')[0] ?? '';
		expect(withoutComments(starter)).not.toContain('setPageInset');
		expect(loadDefault).toContain("setPageInset(editor, '0')");
		// No shell of its own: `max-width:600px` is a nested card. Scoped to that tag — the CTA legitimately has a fill.
		expect(STARTER_HTML).not.toMatch(/max-width:\s*600px/);
		expect(/^<table[^>]*>/.exec(STARTER_HTML)?.[0]).not.toMatch(/background/);
	});

	it('sits flush to the sheet, with no body inset', () => {
		// A 24px body padding left a band that was body rather than page, and the real email's body has no inset.
		expect(canvasCss).toMatch(/body\s*\{[^}]*padding:\s*0/);
	});

	it('top-aligns, so a short email does not float in the middle', () => {
		expect(canvasCss).toMatch(/td\[data-email-page\]\s*\{[^}]*vertical-align:\s*top/);
	});
});

describe('finding the page, and asking whether it is empty', () => {
	/**
	 * THE DEFECT THESE PIN: `pageCell` used `Component.find`, which matches the RENDERED element, so before the
	 * frame's views exist it answers "no page" and every caller took the wrapper fallback — neither seeder was
	 * ever offered, and an inserted field landed beside the page, running the canvas away to 459,595px.
	 */
	it('walks the models to the page cell, with no rendered element in sight', () => {
		const { editor, cell } = fakeEditor([]);
		ensurePageCell(editor);
		expect(pageCell(editor)).toBeDefined();
		expect(pageCell(editor)?.getAttributes?.()?.[PAGE_ATTR]).toBe('true');
		expect(pageCell(editor)?.get?.('tagName')).toBe('td');
		expect(cell).toBeDefined();
	});

	it('a freshly wrapped canvas IS empty — the page is chrome, not content', () => {
		const { editor } = fakeEditor([]);
		ensurePageCell(editor);
		expect(isCanvasEmpty(editor)).toBe(true);
	});

	it('a canvas whose page holds something is not', () => {
		const { editor, cell } = fakeEditor([node({}, [])]);
		ensurePageCell(editor);
		cell.children.push(node({}, []));
		expect(isCanvasEmpty(editor)).toBe(false);
	});

	it('falls back to the wrapper when there is no page at all, and still answers about CONTENT', () => {
		const bare = (children: unknown[]) => ({
			getWrapper: () => ({
				components: () => ({
					map: <T>(fn: (c: unknown) => T) => children.map(fn),
					length: children.length
				})
			})
		});
		expect(isCanvasEmpty(bare([]))).toBe(true);
		expect(isCanvasEmpty(bare([{}]))).toBe(false);
	});

	it('answers rather than throwing for an editor that is not there yet', () => {
		expect(isCanvasEmpty(undefined)).toBe(true);
		expect(isCanvasEmpty({})).toBe(true);
		expect(() => pageCell(undefined)).not.toThrow();
	});
});

/** THE WRAP MUST NOT REACH THE UNDO STACK: one click of Starter layout measured TEN entries, so it runs in `UndoManager.skip`. */
describe('the page wrap and the undo stack', () => {
	it('does every model mutation with tracking paused', () => {
		const fake = fakeEditor([node(), node(), node()]);
		ensurePageCell(fake.editor);
		// It really did work — otherwise "all of nothing was skipped" would pass vacuously.
		expect(fake.skipped.length).toBeGreaterThan(0);
		expect(fake.allWorkWasSkipped(), 'a mutation escaped UndoManager.skip').toBe(true);
	});

	it('still wraps when the editor has no UndoManager', () => {
		// A missing manager must cost the undo pause, never the wrap: losing the wrap loses drop-anywhere for the session.
		const fake = fakeEditor([node()]);
		const { UndoManager, ...withoutManager } = fake.editor as Record<string, unknown>;
		expect(UndoManager).toBeTruthy();
		ensurePageCell(withoutManager);
		expect(fake.appended).toHaveLength(1);
		expect(fake.cell.props.droppable).toBe(true);
	});
});

/** AN IMAGE WHOSE SOURCE IS A MERGE FIELD MUST NOT BE FETCHED. `{{ brand.logo_url }}` is not a URL,
 *  so the canvas resolved it as a relative path: a 404 on every editor open and the browser's broken
 *  -image glyph where the firm's logo belongs — which reads as "the logo is broken" when nothing is
 *  wrong. Reported independently by two QA passes.
 *
 *  THE MODEL IS NEVER TOUCHED. GrapesJS exports an image from its model, so a `set('src', …)` here
 *  would silently ship a blank placeholder to every recipient — the one mistake this must not make. */
describe('a merge-token image', () => {
	const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');

	it('recognises a token source and nothing else', () => {
		expect(isTokenSource('{{ brand.logo_url }}')).toBe(true);
		expect(isTokenSource('{% if x %}a{% endif %}')).toBe(true);
		expect(isTokenSource('https://cdn.example.com/logo.png')).toBe(false);
		expect(isTokenSource('')).toBe(false);
		expect(isTokenSource(undefined)).toBe(false);
	});

	it('claims only an IMG carrying one', () => {
		let spec: { isComponent: (el: unknown) => unknown } | undefined;
		registerEmailImage({ DomComponents: { addType: (_id: string, s: never) => (spec = s) } });
		const el = (tagName: string, src: string) => ({ tagName, getAttribute: () => src });
		expect(spec?.isComponent(el('IMG', '{{ brand.logo_url }}'))).toEqual({ type: 'email-image' });
		expect(spec?.isComponent(el('IMG', 'https://x/y.png'))).toBeUndefined();
		expect(spec?.isComponent(el('SPAN', '{{ brand.logo_url }}'))).toBeUndefined();
	});

	it('overrides only the VIEW, and only for a token', () => {
		// A real URL still renders, so replacing the merge field with a hosted image shows it at once.
		const block =
			source.split('export function registerEmailImage')[1]?.split('\nexport ')[0] ?? '';
		expect(block).toContain("extend: 'image'");
		expect(block).toContain("extendView: 'image'");
		expect(block).toMatch(/if \(!isTokenSource\(src\)\) return baseUpdateSrc/);
		// The element, never the model: `el.src`, and no `model.set` anywhere in the type.
		expect(block).toMatch(/el\.src =/);
		expect(block).toContain('TOKEN_IMAGE_PLACEHOLDER');
		// TWO STAND-INS, chosen on the arm. `masthead-image` extends this view, and the generic
		// stand-in is drawn INTO the element — which covered the firm's logo, painted behind it by
		// `gjs-email-canvas.css`. The masthead gets a transparent pixel so the paint shows through.
		expect(block).toContain('MASTHEAD_PLACEHOLDER');
		expect(block).toMatch(/getAttribute\(BRAND_ARM_ATTR\) === BRAND_ARM_LOGO/);
		expect(block).not.toMatch(/model\??\.set/);
	});

	it('calls the BUILT-IN updateSrc by identity, never through the prototype chain', () => {
		// `masthead-image` extends `email-image`, so `this.constructor.__super__.updateSrc` resolves to
		// the override itself and recurses: a real URL on the masthead blew the stack. It only fired for
		// a NON-token source, which is why every test on `{{ brand.logo_url }}` passed.
		const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');
		const block =
			source.split('export function registerEmailImage')[1]?.split('BROWSERn')[0] ?? source;
		expect(block).toMatch(/const baseUpdateSrc = editor.DomComponents\?\.getType\?\.\('image'\)/);
		expect(block).toMatch(/return baseUpdateSrc\?\.call\(this\)/);
		// Comments stripped: the note above the capture NAMES the idiom it replaced, which a
		// file-wide scan reads as the idiom itself — the false positive `switch-columns.test.ts`
		// records hitting.
		const code = source.replace(/^\s*\/\/.*$/gm, '');
		expect(code).not.toMatch(/__super__/);
	});

	it('builds a placeholder whose colour actually survives encoding', () => {
		// The `#` is encoded ONCE, by `encodeURIComponent` over the whole string. Written `%23` it was
		// encoded again to `%2523`, the colour was invalid, and the placeholder rendered solid black.
		expect(source).toMatch(/stroke="#[0-9a-f]{6}"/);
		expect(source).not.toContain('fill="%23');
		expect(source).not.toContain('stroke="%23');
	});

	it('draws a PICTURE, not a filled grey box', () => {
		// Two reasons, and the second is why the fill matters rather than just the look. (1) A solid
		// rectangle says "something is wrong here" about an image that is perfectly fine and will
		// arrive at send; a framed glyph says "a picture goes here". (2) A replaced image paints over
		// its own background, and the brand masthead is painted behind this element — so an OPAQUE
		// stand-in hid a firm's real logo. There is no `fill` on a shape that covers the box.
		const placeholder = /const TOKEN_IMAGE_PLACEHOLDER =([\s\S]*?)\n\t\);/.exec(source)?.[1] ?? '';
		expect(placeholder, 'TOKEN_IMAGE_PLACEHOLDER not found').not.toBe('');
		expect(placeholder).toContain('fill="none"');
		expect(placeholder).not.toMatch(/<rect width="160" height="40"[^>]*fill="#/);
		// The masthead's own stand-in shows nothing at all, so the canvas paint is what is seen.
		// `;\r?\n`, not `;\n`: this file is checked out CRLF on Windows, where `;` is followed by
		// `\r` and the lazy group can never reach a bare `\n` — the match failed, the capture
		// defaulted to '', and the assertion read as "the masthead paints something" on a constant
		// that was perfectly correct. The same Windows-only shape `d591014e` fixed for the
		// display-case scan.
		const masthead = /const MASTHEAD_PLACEHOLDER =([\s\S]*?);\r?\n/.exec(source)?.[1] ?? '';
		expect(masthead).toContain('width="1" height="1"');
		expect(masthead).not.toContain('fill=');
	});
});

/** THE MASTHEAD IS SCOPED TO ITSELF. The wordmark fallback rode a GENERIC `data-fallback`, so it
 *  belonged to no particular image: duplicate the masthead and the compiled email carried a SECOND
 *  wordmark branch. The arm marker is the identifier now, on both sides — the compile step requires it
 *  (`compile.test.ts`) and the component type claims it here. */
describe('the masthead image', () => {
	const registered = () => {
		let spec: {
			isComponent: (el: unknown) => unknown;
			model: { defaults: Record<string, unknown> };
		};
		registerMastheadImage({
			DomComponents: { addType: (_id: string, s: never) => (spec = s) }
		});
		return spec!;
	};

	it('claims an IMG by its arm marker, and nothing else', () => {
		const { isComponent } = registered();
		const el = (attrs: Record<string, string>, tagName = 'IMG') => ({
			tagName,
			getAttribute: (k: string) => attrs[k] ?? null
		});
		expect(isComponent(el({ [BRAND_ARM_ATTR]: BRAND_ARM_LOGO }))).toEqual({
			type: 'masthead-image'
		});
		expect(isComponent(el({ src: '{{ brand.logo_url }}' }))).toBeUndefined();
		expect(isComponent(el({ [BRAND_ARM_ATTR]: 'wordmark' }))).toBeUndefined();
		expect(isComponent(el({ [BRAND_ARM_ATTR]: BRAND_ARM_LOGO }, 'DIV'))).toBeUndefined();
	});

	it('cannot be COPIED — a second masthead is not a thing to want', () => {
		// This is the load-bearing half of the scoping: a duplicate carried the fallback attribute with
		// it, so the email compiled two wordmark branches.
		expect(registered().model.defaults.copyable).toBe(false);
	});

	it('extends the token-aware image, so it still does not fetch its own token', () => {
		const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');
		const block =
			source.split('export function registerMastheadImage')[1]?.split('\nexport ')[0] ?? '';
		expect(block).toContain("extend: 'email-image'");
		expect(block).toContain("extendView: 'email-image'");
	});

	it('ships the marker in the authored html, from the shared constants', () => {
		expect(BRAND_LOGO_HTML).toContain(`${BRAND_ARM_ATTR}="${BRAND_ARM_LOGO}"`);
	});
});

describe('double-clicking an image', () => {
	const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');

	it('is a no-op, so the absent asset manager never flickers open', () => {
		let id: string | undefined;
		let spec: { view?: { onActive?: (ev?: unknown) => void } } | undefined;
		registerImageActivation({
			DomComponents: {
				addType: (typeId: string, s: never) => {
					id = typeId;
					spec = s;
				}
			}
		});
		// The BUILT-IN type, so both derived image types inherit the override.
		expect(id).toBe('image');
		// No `extend`/`extendView`: an id already registered is extended, not replaced.
		expect(spec).not.toHaveProperty('extend');
		expect(spec).not.toHaveProperty('extendView');

		// Whatever it is handed, it opens nothing and swallows the event.
		const stopPropagation = vi.fn();
		expect(() => spec?.view?.onActive?.({ stopPropagation })).not.toThrow();
		expect(stopPropagation).toHaveBeenCalledTimes(1);
		// Bubbling stays stopped, or the cell behind the image starts editing instead.
		expect(() => spec?.view?.onActive?.(undefined)).not.toThrow();
	});

	it('never reaches the asset manager', () => {
		const block =
			source.split('export function registerImageActivation')[1]?.split('\nexport ')[0] ?? '';
		expect(block).not.toMatch(/Assets|am\.open|open-assets/);
	});

	it('is registered BEFORE the types that extend the image view', () => {
		// `extendView: 'image'` resolves the view at addType time, so a later override would not be
		// inherited by `email-image`/`masthead-image` and a plain image would behave differently.
		const wiring = source.slice(source.lastIndexOf('registerImageActivation(editor)'));
		expect(wiring).toMatch(
			/registerImageActivation\(editor\);[\s\S]*registerEmailImage\(editor\);[\s\S]*registerMastheadImage\(editor\);/
		);
	});
});
