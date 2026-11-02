<script lang="ts">
	/* eslint-disable @typescript-eslint/no-explicit-any */
	// WYSIWYG email editor — a thin Svelte 5 wrapper around GrapesJS + grapesjs-preset-newsletter; the canvas IS the live preview. GrapesJS touches `window`, so everything is dynamic-imported inside onMount (browser only) and SSR/build never load it. Kept deliberately small: no pages, charts, data tables or version history (see the document TemplateEditor). Content flows out via onChange({ html, css }) — the same separate-sheet shape the save action persists (schema.html / schema.css); the `html`/`css` props are read ONCE, to seed the canvas, and are not watched, so the parent updating them from onChange never fights the editor.
	// THE CHROME IS OURS: the block palette and the style rail are `blocks-rail/` and `style-panel/`, built from `@graphene/ui` primitives, because GrapesJS's own views are native `<select>`s, a native `<input type="color">` and Spectrum's popover — three things docs/design-language.md forbids outright. Both managers run `custom: true`, which suppresses their VIEW and nothing else, so every rule in `create.ts` still applies; `style-panel/read.ts` carries the argument for why reading the StyleManager ourselves is safe.
	import { onMount, type Snippet } from 'svelte';
	import { pruneOrphanCss } from './orphan-css.js';
	import { Skeleton } from '../ui/skeleton/index.js';
	import { Button } from '../ui/button/index.js';
	import InspectorSector from '../ui/inspector-sector/inspector-sector.svelte';
	import Segmented from '../ui/segmented/segmented.svelte';
	import PageStyle from './page-style.svelte';
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import { Input } from '../ui/input/index.js';
	import PropertyRow from '../ui/property-row/property-row.svelte';
	import { SelectMenu } from '../ui/select-menu/index.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import PanelRightIcon from '@lucide/svelte/icons/panel-right';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import SmartphoneIcon from '@lucide/svelte/icons/smartphone';
	import Undo2Icon from '@lucide/svelte/icons/undo-2';
	import Redo2Icon from '@lucide/svelte/icons/redo-2';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ChevronsUpIcon from '@lucide/svelte/icons/chevrons-up';
	import ChevronsDownIcon from '@lucide/svelte/icons/chevrons-down';
	import {
		componentDisplayName,
		createEmailEditor,
		deviceWidth,
		FIELD_ATTR,
		FIELD_FALLBACK_ATTR,
		FIELD_FILTER_ATTR,
		fieldSpanHtml,
		readFieldSpan,
		ensurePageCell,
		isCanvasEmpty,
		pageCell,
		readPageStyle,
		type PageStyle as PageStyleValues,
		setPageInset,
		setPageStyle,
		endTextEditing,
		loadEmailContent,
		previewBrandMasthead,
		syncHeadWidth,
		redoEmailEdit,
		syncFrameHeight,
		undoEmailEdit,
		undoEmailEdits,
		undoHistory,
		STARTER_HTML
	} from './create';
	import {
		conditionOnlyFieldsFor,
		mergeFieldsFor,
		repeatableFieldsFor,
		type MergeFieldGroup
	} from './merge-fields';
	import {
		describeFallback,
		describeFilter,
		fieldWrite,
		FIELD_FILTERS,
		fieldToken,
		NO_FILTER,
		NO_FILTER_LABEL,
		parseFieldToken
	} from './field-filters.js';
	import {
		compileConditionals,
		hasAuthoredConditions,
		stripEditorAttrs
	} from './conditions/compile.js';
	import {
		decodeFallback,
		decodeRepeat,
		decodeVisibility,
		encodeFallback,
		encodeRepeat,
		FALLBACK_ATTR,
		REPEAT_ATTR,
		IF_ATTR,
		visibilityAttributes,
		type ImageFallback,
		type ItemRepeat
	} from './conditions/attrs.js';
	import { fieldOptions } from './conditions/fields.js';
	import type { Visibility } from './conditions/model.js';
	import VisibilityEditor from './conditions/visibility-editor.svelte';
	import { canMove, moveTargetIndex, type MoveDirection, type SelectionPosition } from './position';
	import PaintbrushIcon from '@lucide/svelte/icons/paintbrush';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import CodeView, { type CodeShape } from './code-view/code-view.svelte';
	import BlocksRail from './blocks-rail/blocks-rail.svelte';
	import CanvasMenu from './canvas-menu.svelte';
	import type { CanvasMenuAction, CanvasMenuSubject } from './canvas-menu.js';
	// The SHARED rail, not a copy beside this editor. There were two for nine days — this editor's
	// original and the extraction the document editor mounts — and they drifted: the shared one
	// gained the too-wide-choice dropdown, while this one held a border-colour fix the shared one
	// lacked. Both are in the shared copy now, and there is one place to change next time.
	import StylePanel from '../grapes/style-panel/style-panel.svelte';
	import type { StyleManagerLike } from '../grapes/style-panel/read.js';
	import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import RepeatIcon from '@lucide/svelte/icons/repeat';
	import SplitIcon from '@lucide/svelte/icons/split';
	import ImageSource from './image-picker/image-source.svelte';
	import { isPlaceholderSource, isUsableInEmail } from './image-picker/source.js';

	let {
		html = '',
		css = '',
		readOnly = false,
		code = null,
		fields = null,
		onreference,
		interceptInsert,
		oncanvasfocus,
		brandColours = [],
		brandLogoUrl = null,
		brandName = null,
		defaultBody = null,
		imagesUrl = 'images',
		documentHeader,
		onLoadDefault,
		onChange
	}: {
		html?: string;
		css?: string;
		readOnly?: boolean;
		// The template's code — scopes the Insert-field menu to the tokens that code's send site provides (a custom/unknown code gets the full set).
		code?: string | null;
		// An EXPLICIT Insert-field menu, overriding the per-code lookup — for a caller whose tokens aren't a template code.
		fields?: MergeFieldGroup[] | null;
		/** Open the context panel. Offered from the rail as well as the page's own menu, because the
		 *  question comes up while an author is looking at the field list. Absent ⇒ no button. */
		onreference?: () => void;
		/** Consulted BEFORE the canvas insert. Return true to say "I took it" — the page uses this for
		 *  the subject and preview line, which are its own inputs, so ONE affordance (the rail's field
		 *  menu) serves all three places rather than each growing a button of its own. */
		interceptInsert?: (token: string) => boolean;
		/** The author is working in the CANVAS now (they selected something). The page clears its
		 *  remembered head caret on this, so the next insert goes where they are actually looking. */
		oncanvasfocus?: () => void;
		// The firm's own colours, offered as one-click swatches in the style panel's colour controls. Empty ⇒ no swatches, the picker alone. A live prop rather than a GrapesJS construction option, so a change reaches the panel without a remount.
		brandColours?: readonly string[];
		/** The firm's masthead image as STORED — the resolved one, whichever of its two images the
		 *  firm's own `logo_variant` names. Painted behind the Brand logo block's token `src`, which
		 *  can never load (a merge token is not a URL). */
		brandLogoUrl?: string | null;
		/** What the masthead falls back to when the firm has no image: its name, drawn as an SVG so
		 *  an `<img>` can show it. Empty ⇒ the slot reads as an image placeholder. */
		brandName?: string | null;
		defaultBody?: { html: string; css: string } | null;
		// The app's proxy for the firm's image library — GET lists it, POST (multipart) adds to it. The default is RELATIVE, and resolved against the editor's own route, because both mounts put the proxy beside the page they mount from (`/email-templates/{uid}/edit` → `/email-templates/{uid}/images`, and the admin per-tenant editor likewise). A mount whose app has no such route answers 404, which the picker reads as "no library here" and falls back to a pasted link — so an unproxied mount degrades rather than reporting a failure. `null` skips the fetch entirely.
		imagesUrl?: string | null;
		// The email's own head — From / Reply-To / Subject / Preview — rendered at the top of the CANVAS column, on the sheet, above the frame. A snippet rather than four props because the caller owns that state: subject and preheader are form fields posted to `?/save`, and the sender is resolved server-side. This package supplies the position and the width; the page supplies the rows.
		documentHeader?: Snippet;
		onLoadDefault?: () => void;
		onChange: (v: { html: string; css: string; source: string }) => void;
	} = $props();

	// Insert-field groups: an explicit set wins, else scoped to this template's code.
	const menuGroups = $derived(fields ?? mergeFieldsFor(code));

	let canvasEl: HTMLElement;
	// Where GrapesJS renders its OWN block palette: the icon rail's flyout. What an author drags must be GrapesJS's element, not one of ours — see `create.ts`'s `blockManager` note for why.
	let blocksEl: HTMLElement;
	// The workspace and the scrolling canvas column. Watched so GrapesJS's tool layer can be told to
	// re-measure: its highlighter, badge and toolbar are drawn from a CACHED canvas offset, and nothing
	// called `refresh`, so a resize or a panel toggle left every marker where the canvas used to be.
	let workspaceEl: HTMLElement | undefined = $state();
	let columnEl: HTMLElement | undefined = $state();
	// The email's head. Stays right here, in the canvas column above the mount — the column is the scroller, so it scrolls with the email, and `syncHeadWidth` matches it to the card.
	let headEl: HTMLElement | undefined = $state();

	let editor: any = null;
	let ready = $state(false);
	let mountError = $state<string | null>(null);
	let device = $state<'Desktop' | 'Mobile portrait'>('Desktop');
	let canvasEmpty = $state(true);
	let selectedName = $state<string | null>(null);
	// Whether the style rail is showing. A view preference, so it is NOT persisted anywhere near the template: a collapse must never end up in a saved blob (the same rule the style panel's sector overrides follow).
	let railOpen = $state(true);
	// THE HTML CODE VIEW — whether the code pane is showing, a view preference like `railOpen` and never persisted either. IT SHIFTS THE EMAIL RIGHT while it is open (`--code-gutter` on `.canvas-column`) where the style panel deliberately displaces nothing at all. Do not "fix" one to match the other: the style panel configures the thing you are looking at, so moving that thing while you configure it is disorienting (the arithmetic, and the gutter that must never come back, are on `.workspace`), whereas the code view is a second view of the same document meant to be read ALONGSIDE the email — overlaying it would cover the very thing you opened it to compare against.
	let codeOpen = $state(false);
	// SENT IS THE DEFAULT SHAPE, because the question someone opens a code view to ask is "what will my clients actually receive". The editor shape answers a narrower, later question and its conditions are percent-encoded JSON (`data-if="%7B%22action%22%3A%22show%22…"`), unreadable by construction. See `codeHtml` for the two shapes, and `canEdit` in `code-view.svelte` for why only one is editable.
	let codeShape = $state<CodeShape>('sent');
	// What the style panel was doing before the code view took the canvas — the two never show at once. REMEMBERED rather than assumed, because opening the code view must not permanently collapse a panel the author had open, and must not reopen one they had deliberately put away. Not `$state`: it drives no markup.
	let railOpenBeforeCode = true;

	function toggleCode(): void {
		if (codeOpen) {
			codeOpen = false;
			railOpen = railOpenBeforeCode;
			return;
		}
		railOpenBeforeCode = railOpen;
		railOpen = false;
		codeOpen = true;
	}
	let menuOpen = $state(false);
	let menuSubject = $state<CanvasMenuSubject | null>(null);
	let menuTrigger = $state<HTMLElement | null>(null);
	let canvasMenuDoc: Document | null = null;
	let pageStyle = $state<PageStyleValues>({ background: '', padding: '' });

	function refreshPageStyle(): void {
		pageStyle = readPageStyle(editor);
	}
	// The curated inspector — the attributes that genuinely matter in an email and are otherwise UNREACHABLE: a link's destination (every block ships the `#` placeholder) and an image's alt text. See the panel markup for why GrapesJS's own trait rail is dropped rather than restyled.
	let linkTarget = $state<any>(null);
	let imageTarget = $state<any>(null);
	let imageFallback = $state<ImageFallback | null>(null);
	// The selection's REPEAT — the list it is written once per. It replaces a hand-typed `{% for item in item_titles %}` around an example row, which rendered as visible lines of code on the canvas and made the editor and the preview disagree about the email's height.
	let repeat = $state<ItemRepeat | null>(null);
	let linkHref = $state('');
	let imageAlt = $state('');
	let imageSrc = $state('');
	let inspectorOpen = $state(true);
	let inspectedCid: string | null = null;
	let position = $state<SelectionPosition | null>(null);
	let visibility = $state<Visibility | null>(null);
	// Open when the selection HAS a condition — an author who set one wants to see it on reselect, and one who has not is not asked about it before they have asked to be; the Repeat band follows the same rule.
	let visibilityOpen = $state(false);
	let repeatOpen = $state(false);
	let field = $state<{ field: string; filter: string; fallback: string } | null>(null);
	// The palette's own category labels, in its order — one rail segment each. Read from GrapesJS rather than listed here, so a category the palette gains gets a segment for free.
	let blockCategories = $state<string[]>([]);
	// The fields a condition may ask about: this template's own send-site vocabulary, never more — the insertable tokens PLUS the askable-but-not-printable half (a boolean or a URL is a thing to ask about, never a thing to print, so it is absent from the insert menu this derives from). Scoped by `code`, not by `fields`: an override supplies what may be INSERTED, and the send site's own booleans are not the caller's to redefine.
	const conditionFields = $derived(fieldOptions(menuGroups, conditionOnlyFieldsFor(code)));
	// Scoped by `code` like the condition vocabulary: a loop over a list the send site never passes renders nothing, and a row that silently vanishes is worse than one that was never offered.
	const repeatChoices = $derived(repeatableFieldsFor(code));
	// Undo/redo affordance. Enablement is read from the UndoManager rather than assumed: a permanently live Undo button that does nothing is a worse lie than no button, and after `trackSelection: false` (see create.ts → UNDO) `hasUndo()` is trustworthy — every step on the stack is a visible change.
	let canUndo = $state(false);
	let canRedo = $state(false);
	let undoOpen = $state(false);
	let undoHover = $state(-1);
	let undoList = $state<{ label: string }[]>([]);

	// The StyleManager, held separately from `editor` so the panel is reactive. `editor` stays a plain `let` deliberately: it is a GrapesJS class instance, and putting it in `$state` would hand it to Svelte's deep proxy — which does not wrap class instances today, but relying on that is a bet on an implementation detail with a very bad failure mode.
	let styleManager = $state<StyleManagerLike | undefined>(undefined);
	let revision = $state(0);

	// THE DOCUMENT THE CODE PANE SHOWS, in whichever of `read()`'s two shapes is selected. BOTH ARE OFFERED because they answer two different questions, and showing one while calling it the other is how you debug the wrong document; only `source` is editable, since `html` is derived. Re-read on `revision` rather than cached, so the pane is a live view of the canvas beside it, and guarded on `codeOpen` so a closed pane costs nothing: `read()` compiles the whole document and prunes the stylesheet.
	const codeHtml = $derived.by(() => {
		if (!codeOpen || !ready) return '';
		void revision;
		const content = current();
		if (!content) return '';
		return codeShape === 'sent' ? content.html : content.source;
	});

	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;
	let stopFrameSync: (() => void) | null = null;
	let stopHeadMount: (() => void) | null = null;
	let stopBrandPreview: (() => void) | null = null;

	/** The brand pair the canvas is painted for. One string so a change in either half is one test. */
	const brandKey = (logo: string | null | undefined, name: string | null | undefined): string =>
		`${logo ?? ''}|${name ?? ''}`;
	let appliedBrand: string | null = null;

	// THE CANVAS FOLLOWS THE BRAND, not just the brand as it stood at mount. `previewBrandMasthead`
	// was called once inside the mount block with the prop values at that instant and never again:
	// its own re-apply hooks are `load` and `canvas:frame:load`, which are about the FRAME's
	// lifecycle, not about the props. So a firm that changed its logo while this editor was open —
	// `onPublished` calls `invalidateAll()`, which re-runs the loader that resolves `data.branding`
	// — kept seeing the old one until a reload, with nothing to say the canvas was stale.
	//
	// Guarded on the VALUES, not on the effect running: the first run after mount must be a no-op
	// (the mount block has already painted this pair and registered the frame listeners in the
	// right order), and a re-render that changes neither half must not tear a working preview down
	// and build it again.
	$effect(() => {
		const key = brandKey(brandLogoUrl, brandName);
		if (!ready || !editor || key === appliedBrand) return;
		appliedBrand = key;
		stopBrandPreview?.();
		stopBrandPreview = previewBrandMasthead(editor, brandLogoUrl, brandName);
	});

	// The editor's content, in BOTH shapes. `source` is what the canvas holds — including the `data-if` attributes an authored condition lives in; `html` is the render-ready form, with those compiled into Jinja and the attributes stripped. Neither can be recovered from the other where it is used: the send path reads `html` and must never receive editor bookkeeping, and re-opening a template needs the structured condition back, which parsing Jinja could not give us. Templates with no conditions skip the compile entirely and their two values are identical.
	function read(): { html: string; css: string; source: string } {
		const source = editor?.getHtml?.() ?? '';
		return {
			source,
			// STRIPPED ON BOTH BRANCHES. `compileConditionals` strips the editor's own attributes as it walks, so only a template WITH authored conditions comes out clean that way; one without them is handed straight through and would carry `data-field`, `data-field-filter`, `data-field-fallback` and `contenteditable="false"` into the previewed and emailed document. `source` keeps them, because it is what the canvas is re-seeded from and `ensurePageCell`/`readFieldSpan` identify their own work by them.
			html: stripEditorAttrs(hasAuthoredConditions(source) ? compileConditionals(source) : source),
			// PRUNED. `getCss()` re-emits every inline declaration a second time as an `#id` rule, and none of them can ever match — the compiled document carries no `id` attributes, and GrapesJS mints new ids on every parse, so an untouched template produced a different CSS string every session and every save stored a diff no author made. See `pruneOrphanCss`.
			css: pruneOrphanCss(
				(editor?.getCss?.({ avoidProtected: true }) as string | undefined) ?? '',
				source
			)
		};
	}

	// The editor's current content, read synchronously — or `null` while the GrapesJS instance is still loading, so a caller can fall back to the seeded content instead of overwriting it with empties (an unready `current()` must never be mistaken for "the canvas is empty").
	export function current(): { html: string; css: string; source: string } | null {
		return editor ? read() : null;
	}

	// The content last handed to `onChange`, so an emit that changes nothing can be suppressed. Seeded from `read()` once the canvas has loaded — NOT from the `html`/`css` props: GrapesJS re-serialises what it parses (it inserts `<tbody>`, reorders attributes, normalises spacing), so the props and the first `getHtml()` differ for content nobody has touched, and comparing against the props would report a change on load.
	let lastEmitted: { html: string; css: string; source: string } = {
		html: '',
		css: '',
		source: ''
	};

	function emitNow(): void {
		if (!editor || destroyed) return;
		const next = read();
		// COMPARE CONTENT; never trust the event. GrapesJS reports a model change for things the AUTHOR did not do — selecting a component mints an id for the StyleManager to target, and blurring text fires rte:disable — and the page turns any emit into `dirty = true`. So an emit per event means merely CLICKING a block marks the template unsaved, which is not cosmetic: Publish is disabled while dirty, so a click blocks publishing until you save, and the save writes a no-op version — which on the admin door also stamps authored_by_scope='operations', telling a firm Graphene edited something it did not. Comparing content also means an edit the author manually reverts correctly clears dirty again. Compared on SOURCE, not on the compiled html: two different sources cannot compile to the same html without the difference being editor bookkeeping, which is still a change worth saving.
		if (next.source === lastEmitted.source && next.css === lastEmitted.css) return;
		lastEmitted = next;
		onChange(next);
	}

	function scheduleEmit(): void {
		if (debounceTimer) clearTimeout(debounceTimer);
		debounceTimer = setTimeout(emitNow, 250);
	}

	// A click on the BACKDROP clears the selection, which puts the rail back to Page style. THE GUARD IS "NOT THE HEAD", not "the column itself": a click inside the email never reaches this handler at all (the canvas is an iframe, so it goes to that document), and `target === currentTarget` matches NOTHING, because the canvas mount spans the column's full width and the grey either side of the card is its box, not the column's. The head sheet is the one real exception — it lives in THIS document, so a click on the Subject field would otherwise clear the selection under the author while they typed.
	/** Which of the rail's flyouts is showing. Held HERE, not in the rail, because a click on the
	 *  canvas — the obvious way to dismiss a panel covering it — happens outside the rail entirely. */
	let railFlyout = $state<string | null>(null);

	function deselectOnBackdrop(event: MouseEvent): void {
		const target = event.target as HTMLElement | null;
		if (target?.closest?.('.gjs-email-head')) return;
		// A CLICK, deliberately: a drag out of the palette and onto the canvas fires none on release,
		// so dragging a block in cannot close the flyout out from under the drag.
		railFlyout = null;
		editor?.select?.(null);
	}

	// "← Page style" — the way back UP from a selection. It DESELECTS: the page's own background and inset are only reachable when nothing is selected (the page is deliberately unselectable, see `protectPage`), so "go to Page style" and "deselect" are the same act. `select(null)` alone, exactly as `deselectOnBackdrop` does, because two paths to one state is how they end up disagreeing — the `component:deselected` listener runs `refreshSelection`.
	function backToPageStyle(): void {
		editor?.select?.(null);
	}

	function refreshEmpty(): void {
		// `isCanvasEmpty`, NOT a wrapper-child count: the page cell `ensurePageCell` installs IS a wrapper child, so the count is 1 on a blank template and the Starter-layout and Load-default offers could never appear on the one canvas they are for.
		canvasEmpty = isCanvasEmpty(editor);
		// The page's own style can move from four directions too (this panel, an undo, a seeder standing the inset down, a re-parse), so it is re-read on the same structural signal rather than cached.
		refreshPageStyle();
	}

	function refreshSelection(): void {
		const sel = editor?.getSelected?.();
		revision += 1;
		if (!sel) {
			selectedName = null;
			linkTarget = null;
			imageTarget = null;
			inspectedCid = null;
			// EVERY per-selection value, not just the four above. This branch returns early, so anything it forgets keeps the value the LAST selection left behind — a deselect would leave the Field sector sitting above Page, describing a merge field nothing points at. The two syncs below read `getSelected()` themselves and answer null for no selection, so calling them is the same clearing the selected branch does rather than a second list to keep in step.
			syncFieldsFromSelection();
			syncVisibilityFromSelection();
			refreshPosition();
			return;
		}
		selectedName = componentDisplayName(sel);
		linkTarget = findLink(sel);
		imageTarget = isImage(sel) ? sel : null;
		// Open the inspector for a NEWLY inspected component. It is the only route to a link's destination and an image's alt text, so a collapsed-by-default section would hide the one thing the author selected the block to reach — but re-syncing the same selection (a style edit, an undo) leaves their own collapse alone.
		const cid = (linkTarget ?? imageTarget)?.cid ?? null;
		if (cid && cid !== inspectedCid) inspectorOpen = true;
		inspectedCid = cid;
		syncFieldsFromSelection();
		syncVisibilityFromSelection();
		refreshPosition();
	}

	// Read the selection's condition. Opened for a selection that HAS one, so it is visible without hunting; left alone otherwise, so the section does not demand attention it has not earned.
	function syncVisibilityFromSelection(): void {
		const sel = editor?.getSelected?.();
		visibility = sel ? decodeVisibility(sel.getAttributes?.()?.[IF_ATTR]) : null;
		if (visibility) visibilityOpen = true;
		field = sel ? readFieldSpan(sel) : null;
	}

	function applyFieldFilter(next: string): void {
		writeField({ filter: next });
	}

	// WHAT THE FIELD SHOWS WHEN THERE IS NOTHING. `{{ first_name }}` for a client with no first name on record renders nothing, so the email goes out saying "Hi ,". It is a FILTER rather than Jinja's `or` for the reason `field-filters.ts` sets out in full: `or` fires on anything falsy, so a real £0.00 balance would be reported to a client as the placeholder. `fallback` fires only on nothing.
	function applyFieldFallback(next: string): void {
		writeField({ fallback: next });
	}

	// Rewrite the selected field span — attributes and token text together, both from `fieldSpanHtml`'s own builders — so the structured value and the Jinja that will actually be evaluated cannot disagree. Both controls go through here for that reason: two write paths is how they would come apart. The author never types the Jinja, and `FIELD_FILTERS` offers only filters the send environment registers, because one it does not is a template error raised at SEND, on a real client's email, long after this looked right.
	function writeField(patch: { filter?: string; fallback?: string }): void {
		const sel = editor?.getSelected?.();
		if (!sel || readOnly || !field) return;
		// ONE STATE, both writes — `fieldWrite` derives the attributes and the token together, so the
		// value the panel reads back and the Jinja the send evaluates cannot come apart.
		const { next, attributes, token } = fieldWrite(field, patch, {
			field: FIELD_ATTR,
			filter: FIELD_FILTER_ATTR,
			fallback: FIELD_FALLBACK_ATTR
		});
		sel.addAttributes?.(attributes);
		sel.components?.(token);
		field = next;
		emitNow();
	}

	// Write the condition onto the selected component through `addAttributes`, so it is a component MODEL change: it goes on the undo stack with everything else, and it reaches `getHtml()` — which is what the compile step reads. Writing to the DOM element directly would do neither, and the condition would vanish on the next save.
	function applyVisibility(next: Visibility | null): void {
		const sel = editor?.getSelected?.();
		if (!sel || readOnly) return;
		sel.addAttributes?.(visibilityAttributes(next));
		visibility = next;
		// Immediately, not on the 250ms debounce: the canvas tab appears from this attribute, and the page's dirty flag should follow the edit that caused it.
		emitNow();
	}

	function readPosition(): SelectionPosition | null {
		const sel = editor?.getSelected?.();
		const parent = sel?.parent?.();
		const siblings = parent?.components?.();
		if (!sel || !parent || !siblings) return null;
		return {
			index: sel.index?.() ?? 0,
			count: siblings.length ?? 0,
			// `draggable` is `true | false | selector | fn` — only an outright false means "this cannot be moved" (the preset locks some table plumbing that way); a selector constrains WHERE it may be dropped, which a same-parent reorder never leaves.
			movable: sel.get?.('draggable') !== false
		};
	}

	function refreshPosition(): void {
		position = readPosition();
	}

	// Move the selection within its parent. ONE undo step, without any grouping code: backbone-undo fuses every action raised in a single synchronous callstack under one `magicFusionIndex`, and GrapesJS's `undo()` runs in magic mode, so the remove and re-append `move` performs must stay in the same tick — NOTHING HERE MAY AWAIT. Re-selecting afterwards is not cosmetic: `move` removes the component before re-appending it and GrapesJS drops a removed component from the selection, so without it the panel empties after every click and the author has to re-click the block for each further nudge.
	function moveSelection(direction: MoveDirection): void {
		if (!editor || readOnly) return;
		const sel = editor.getSelected?.();
		const parent = sel?.parent?.();
		if (!sel || !parent) return;
		// Re-read rather than trusting the state the buttons are drawn from: a sibling added or removed between the render and the click would otherwise move the block to a stale index.
		const at = moveTargetIndex(direction, readPosition());
		if (at === null) return;
		sel.move(parent, { at });
		editor.select?.(sel);
		refreshPosition();
		emitNow(); // sync + mark dirty now, not on the 250ms debounce
	}

	// Duplicate the selection, next to itself. `clone()` rather than the `core:copy` + `core:paste` pair: those go through the editor's clipboard, so they would overwrite whatever the author had copied. Placed at `index + 1` and selected, so the copy is what the rail points at rather than the original. `copyable: false` is honoured rather than trusted to fail safely: the preset locks some table plumbing that way, and a clone of a `<tr>` is a row the email did not ask for.
	function duplicateSelection(): void {
		if (!editor || readOnly) return;
		const sel = editor.getSelected?.();
		const parent = sel?.parent?.();
		if (!sel || !parent || sel.get?.('copyable') === false) return;
		const clone = sel.clone?.();
		if (!clone) return;
		parent.append?.(clone, { at: (sel.index?.() ?? 0) + 1 });
		editor.select?.(clone);
		refreshPosition();
		emitNow();
	}

	function deleteSelection(): void {
		if (!editor || readOnly) return;
		const sel = editor.getSelected?.();
		if (!sel || sel.get?.('removable') === false) return;
		sel.remove?.();
		editor.select?.(null);
		refreshPosition();
		emitNow();
	}

	// The component under an element, walking up. `el.__cashData.model` is GrapesJS's own route from a DOM node to its component — the property its internal `getModel` reads (grapes.mjs :20164) — and walking up is what makes a click on a `<strong>` inside a paragraph resolve to the paragraph. Depth-bounded like every walk in this editor: an unexpectedly deep tree must degrade to "nothing found", never to a hang.
	function componentAt(target: EventTarget | null): any {
		let el = target as (HTMLElement & { __cashData?: { model?: unknown } }) | null;
		for (let depth = 0; el && depth < 24; depth += 1) {
			const model = el.__cashData?.model;
			if (model) return model;
			el = el.parentElement as typeof el;
		}
		return null;
	}

	// A right-click inside the canvas frame. TWO THINGS HAPPEN, in this order. First the click SELECTS what it landed on, so the menu and the rail cannot disagree about what is being talked about; an unselectable target (the page, and the table plumbing above it) deselects instead, which is how the page's own menu appears — "nothing selected" IS the page's state.
	// Then the event is FORWARDED. `clientX`/`clientY` inside the frame are relative to the frame element, so adding the frame's own position gives the pointer in this document — and the frame is never internally scrolled (`syncFrameHeight` sizes it to its content and the canvas column is the scroller), so there is no scroll offset to add. The synthetic event must NOT be `preventDefault`ed: the trigger's handler returns early on `defaultPrevented`. Calling `preventDefault` on the FRAME's event is what suppresses the browser's own menu, and that is a different object.
	function openCanvasMenu(event: MouseEvent): void {
		// READ-ONLY GETS THE BROWSER'S OWN MENU. Nothing here may be edited, and copying text out of a template you may only read is a real thing to want, which a menu of greyed rows would take away.
		if (readOnly || !editor || !menuTrigger) return;
		event.preventDefault();
		const model = componentAt(event.target);
		editor.select?.(model && model.get?.('selectable') !== false ? model : null);
		refreshSelection();
		const sel = editor.getSelected?.();
		menuSubject = {
			selected: !!sel,
			position: readPosition(),
			hasCondition: !!visibility,
			removable: sel?.get?.('removable') !== false,
			copyable: sel?.get?.('copyable') !== false
		};
		const rect = editor.Canvas?.getFrameEl?.()?.getBoundingClientRect?.();
		menuTrigger.dispatchEvent(
			new MouseEvent('contextmenu', {
				bubbles: true,
				clientX: (rect?.left ?? 0) + event.clientX,
				clientY: (rect?.top ?? 0) + event.clientY
			})
		);
	}

	function runMenuAction(action: CanvasMenuAction): void {
		switch (action) {
			case 'condition':
				railOpen = true;
				visibilityOpen = true;
				return;
			case 'condition-clear':
				return applyVisibility(null);
			case 'move-top':
				return moveSelection('top');
			case 'move-up':
				return moveSelection('up');
			case 'move-down':
				return moveSelection('down');
			case 'move-bottom':
				return moveSelection('bottom');
			case 'duplicate':
				return duplicateSelection();
			case 'delete':
				return deleteSelection();
			case 'page-style':
				railOpen = true;
				return;
		}
	}

	function isLink(c: any): boolean {
		return c?.get?.('type') === 'link' || String(c?.get?.('tagName') ?? '').toLowerCase() === 'a';
	}
	function isImage(c: any): boolean {
		return (
			c?.get?.('type') === 'image' ||
			c?.get?.('type') === 'email-image' ||
			c?.get?.('type') === 'masthead-image' ||
			String(c?.get?.('tagName') ?? '').toLowerCase() === 'img'
		);
	}

	/** The bespoke firm logo — wordmark fallback only while brand image storage is still being stood up. */
	function isBrandLogo(c: any): boolean {
		return c?.get?.('type') === 'masthead-image';
	}

	// The anchor whose href the inspector should edit: the selection itself, or — for a Button block, which is a table wrapping a single `<a>` so the fill can sit on the cell — its one anchor descendant. Returns null when there is more than one, because then "the link" is ambiguous and silently editing the first would be worse than offering nothing.
	function findLink(c: any): any {
		if (isLink(c)) return c;
		const found: any[] = [];
		const walk = (node: any): void => {
			node?.components?.()?.each?.((child: any) => {
				if (isLink(child)) found.push(child);
				walk(child);
			});
		};
		walk(c);
		return found.length === 1 ? found[0] : null;
	}

	function syncFieldsFromSelection(): void {
		const attrs = linkTarget?.getAttributes?.() ?? {};
		linkHref = String(attrs.href ?? '');
		imageAlt = String(imageTarget?.getAttributes?.()?.alt ?? '');
		// GrapesJS's placeholder is a raw `<svg …>` string in the model's `src` — read as NO source, so
		// the field is empty and offers to be filled (see `isPlaceholderSource`).
		const rawSrc = String(imageTarget?.get?.('src') ?? imageTarget?.getAttributes?.()?.src ?? '');
		imageSrc = isPlaceholderSource(rawSrc) ? '' : rawSrc;
		imageFallback = decodeFallback(imageTarget?.getAttributes?.()?.[FALLBACK_ATTR]);
		// On the SELECTION, not on an image: any element can repeat.
		repeat = decodeRepeat(editor?.getSelected?.()?.getAttributes?.()?.[REPEAT_ATTR]);
		if (repeat) repeatOpen = true;
	}

	function applyHref(value: string): void {
		linkHref = value;
		// `#` is the blocks' placeholder; writing it back as a real href would ship a dead CTA, so an emptied field clears the attribute rather than restoring the placeholder.
		linkTarget?.addAttributes?.({ href: value.trim() });
	}

	// Turn repetition on or off for the selection, or point it at a different list. `null` clears it — the row ships once. The ALIAS is kept as whatever it was (or `item` for a new one) rather than offered as a control: it is the name the row's own content already refers to, so changing it here would silently break every `{{ item }}` inside the row.
	function applyRepeat(field: string): void {
		const sel = editor?.getSelected?.();
		if (!sel || readOnly) return;
		const next = field ? { field, alias: repeat?.alias || 'item' } : null;
		sel.addAttributes?.({ [REPEAT_ATTR]: next ? encodeRepeat(next) : null });
		repeat = next;
		emitNow();
	}

	// Write the image's fallback TEXT back onto the element. Only the text is editable: the `field` is what the image's own `src` depends on, so changing it would make the fallback fire on something unrelated to the picture, and `tag`/`style` are the look, which belongs to whoever authored the image. Clearing the text leaves the fallback in place and empty, which is a real answer — "show nothing rather than a broken image".
	function applyImageFallback(text: string): void {
		if (!imageFallback || !imageTarget || readOnly) return;
		const next = { ...imageFallback, text };
		imageTarget.addAttributes?.({ [FALLBACK_ATTR]: encodeFallback(next) });
		imageFallback = next;
		emitNow();
	}

	// Point the selected image at a new source, and take the alt text the picker collected with it. `set('src', …)`, NOT `addAttributes({ src })`: GrapesJS holds an image's source as a MODEL property — the view redraws on `change:src`, and `getAttrToHTML` overwrites any `src` attribute with it — so writing the attribute alone changes the export and leaves the canvas showing the old picture.
	function applyImageSrc(next: string): void {
		if (!imageTarget || readOnly) return;
		imageSrc = next.trim();
		imageTarget.set?.('src', imageSrc);
		emitNow();
	}

	function applyAlt(value: string): void {
		imageAlt = value;
		// Alt text is not decoration in email: many clients block images by default, so this is often the only thing the recipient sees where the image should be.
		imageTarget?.addAttributes?.({ alt: value });
	}

	// Run a whole-document SEED with undo tracking paused. A seed is not a coherent undo target — measured on one click of Starter layout: fourteen entries without this, ten from `ensurePageCell`'s re-wrap (each moved child is a remove plus an add), three from the document replacement, one from standing the page inset down — so ctrl+z did not undo the seed, it dismantled it a paragraph at a time. The pause belongs at the CALL SITE rather than inside the writers: `setPageStyle` is also the Page style panel's writer and must stay undoable there. WHAT IS GIVEN UP: a seed cannot be undone. It costs nothing — both seeders are gated on an EMPTY canvas, so the state an undo would return to is the empty page, and emptying it is one undoable delete.
	function seed(apply: () => void): void {
		const um = editor?.UndoManager;
		if (um?.skip) um.skip(apply);
		else apply();
	}

	function insertStarter(): void {
		if (!editor || readOnly || !canvasEmpty) return;
		// Via loadEmailContent, not setComponents directly: seeding must use the one seam that gets the setStyle/setComponents order right (see its docstring) so a seed can never be the call site that reintroduces the wipe.
		seed(() => loadEmailContent(editor, { html: STARTER_HTML, css: '' }));
		// Re-wrap. `loadEmailContent` calls `setComponents`, which replaces the whole document — page cell included — so without this a seeded canvas loses the drop-anywhere behaviour for the rest of the session, and the symptom is "you cannot drag blocks onto the page" arriving mid-edit with no cause an author could see. Idempotent, so it is safe here and on load.
		ensurePageCell(editor);
		// THE PAGE'S INSET IS LEFT ALONE — do not stand it down here. `STARTER_HTML` is plain content, so the sheet's own inset is what puts it where it belongs; clearing it leaves the copy flush to the page edges. (A shipped default is different: see `loadDefault`.)
		refreshEmpty();
		emitNow(); // sync + mark dirty immediately, not on the debounce
	}

	function loadDefault(): void {
		if (!editor || readOnly || !canvasEmpty || !defaultBody) return;
		seed(() => {
			loadEmailContent(editor, defaultBody);
			ensurePageCell(editor); // see `insertStarter` — setComponents replaces the page cell too
			// The inset IS stood down here, unlike the starter: a shipped default carries the house shell (`email_defaults._shell`) with its own padding, so the page's inset would stack a second one. Inside the seed, because it is something the seeder does on the author's behalf.
			setPageInset(editor, '0');
		});
		refreshEmpty();
		emitNow();
		onLoadDefault?.();
	}

	// REPLACE THE DOCUMENT WITH HAND-EDITED HTML. Only ever reached from the code pane's hold-to-confirm, and only for the `source` shape — the compiled shape is derived and has nowhere to put an edit. The pane refuses to call this when the text has not changed, for the reason `emitNow` documents: a whole-document replacement re-parses and re-serialises, so applying an identical string still moves the content (new ids, inserted `<tbody>`, reordered attributes) and the page would read as unsaved for an edit nobody made. THE STYLESHEET IS CARRIED THROUGH, since the code view shows HTML only, and is read BEFORE the replacement because the parse mints new ids. INSIDE `seed()`, i.e. with undo tracking paused, which the hold-to-confirm discloses in as many words.
	function applyCode(next: string): void {
		if (!editor || readOnly) return;
		const keptCss = (editor.getCss?.({ avoidProtected: true }) as string | undefined) ?? '';
		seed(() => {
			loadEmailContent(editor, { html: next, css: keptCss });
			// See `insertStarter`: `setComponents` replaces the page cell too, and without the re-wrap the canvas loses drop-anywhere for the rest of the session.
			ensurePageCell(editor);
		});
		// The selection cannot survive a document replacement — the component it named is gone — so the panel is put back to Page style rather than left describing something that no longer exists.
		editor.select?.(null);
		refreshEmpty();
		refreshSelection();
		refreshUndo();
		emitNow();
	}

	// Insert a merge-field token into the canvas, at the caret while the author is typing and appended into the selection otherwise. A field goes in as a COMPONENT, not as loose text: that is what makes it selectable, and selectable is what lets the panel offer a format on it — `{{ amount }}` renders `1234.5` into a client's email where the firm means £1,234.50. A plain-text token in an existing template still behaves exactly as it did; neither the highlight overlay nor the send-time merge knows the difference. CLICK ONLY — deliberately no drag, because a field goes in the middle of a sentence and a drop resolves to an element, never to a position in text. The `{{` picker works the same way, so there is one behaviour to learn rather than two.
	/** The markup handed to the frame and not yet answered for — appended here if it refuses. */
	let pendingInsert: string | null = null;

	function insertToken(token: string): void {
		if (readOnly) return;
		// Before anything canvas-shaped: the page's head lines are plain inputs, and a token bound for
		// one of them must not also land in the email.
		if (interceptInsert?.(token)) return;
		if (!editor) return;
		const markup = fieldMarkup(token);
		// AT THE CARET, ALWAYS TRIED FIRST. The caret is a selection inside the canvas IFRAME, so this
		// side cannot see it — the frame performs the insert and ANSWERS, and only its refusal falls
		// back to appending. It used to be gated on `isEditingText()` here, which reads GrapesJS's own
		// editing flag: clicking the rail's field row moves focus out of the canvas and GrapesJS ends
		// the RTE session on the way, so by the time this ran the flag was already false and every
		// insert appended a new block at the END of the email — the opposite of the flyout's own
		// promise, "insert it where you are typing".
		const frame = frameWindow();
		if (frame) {
			pendingInsert = markup;
			frame.postMessage(
				{ type: 'gjs-email-insert-field', html: markup, token },
				window.location.origin
			);
			return;
		}
		appendField(markup);
	}

	/** Place a field where there is no live caret. `intoId` names the component the author was last
	 *  typing in (the frame's own answer) — the field joins that paragraph. Without one it becomes a
	 *  paragraph of its own, which is all that can honestly be done. */
	function appendField(markup: string, intoId?: string): void {
		if (!editor || readOnly) return;
		// The id is the COMPONENT's, and GrapesJS renders it as the element's `id` — see the frame script.
		// SHAPE-CHECKED, because it arrives over the bridge and is interpolated into a SELECTOR: a GrapesJS
		// id is a bare word, so anything else is a malformed answer and nothing to go looking for.
		const id = intoId && /^[A-Za-z0-9_-]+$/.test(intoId) ? intoId : null;
		const into = id ? editor.getWrapper?.()?.find?.(`#${id}`)?.[0] : null;
		if (into) {
			const placed = into.append?.(markup)?.[0];
			if (placed) editor.select?.(placed);
			emitNow();
			return;
		}
		const sel = editor.getSelected?.();
		// THE PAGE CELL, NOT THE WRAPPER. A component appended beside the page table is content OUTSIDE the page, a shape nothing else in this editor produces, and it once broke `syncFrameHeight`'s fixed point: a 41px sibling made every measurement 41px taller than the last and the canvas ran away (observed at 459,595px, the email intact and unreachable inside it). `syncFrameHeight` no longer has that failure mode, but a field belongs in the email regardless — a block placed by click already lands here (`appendBlockOnClick`).
		const target = sel && sel.get?.('droppable') ? sel : pageCell(editor);
		// PARSED, not handed over as a `content` string. A text component given `content` keeps it as
		// raw inner HTML, so the chip never becomes a component at all — `find('[data-field]')` returned
		// nothing, the span could not be selected, and the whole Field panel (its filter and its
		// "If empty") was therefore unreachable for any field placed this way.
		const added = target?.append?.(`<div style="padding:10px">${markup}</div>`)?.[0];
		// Selected, so the panel's Field section opens on what was just placed — the same courtesy a clicked block gets (`appendBlockOnClick`), and the only way the author finds the format control without hunting for it.
		const chip = added?.find?.('[data-field]')?.[0];
		if (chip) editor.select?.(chip);
		else if (added) editor.select?.(added);
		emitNow();
	}

	// A field's markup, from ONE builder. Every path reads it — the caret insert, the append, and the vocabulary the `{{` picker is sent — so no two of them can place different HTML for the same token. The canvas script cannot import this module, so a second copy of `fieldSpanHtml` in there is exactly how the three would come apart; the markup therefore travels WITH each field over the bridge (see `publishPickerFields`). The symptom of a drift would be a format silently lost on save.
	function fieldMarkup(token: string): string {
		const parsed = parseFieldToken(token);
		// Anything that is not a token we can describe goes in as the author's own text. A caller could pass one — the subject line's tokens come through the same vocabulary — and rewriting it into a component we cannot round-trip would be worse than leaving it alone.
		// The FALLBACK travels too: dropping it silently unset what an author had just written with the
		// `If empty` control.
		return parsed ? fieldSpanHtml(parsed.field, parsed.filter, parsed.fallback) : token;
	}

	function setDevice(name: 'Desktop' | 'Mobile portrait'): void {
		device = name;
		editor?.setDevice?.(name);
		// DROP THE HEAD'S STALE INLINE WIDTH, so `--device-w` is what sizes it. `syncHeadWidth` (create.ts) writes an inline `width` onto the head from `.gjs-frame-wrapper`'s measured width, and an inline style beats the stylesheet — so whatever it last wrote is the head's width until it writes again, and it does not write again on a device change. Its `change:device` handler reads the wrapper synchronously, when the wrapper is still the OLD width (measured: 640px read while the settled value was 375px), so it re-writes the previous device's number; and ITS RESIZEOBSERVER IS DEAD — the wrapper is the same connected node throughout and genuinely resizes 640 → 375, yet a fresh ResizeObserver on that exact element fired ZERO times for it. So the head stayed one device behind, in both directions.
		// Clearing the inline value hands the width back to the stylesheet, where `var(--device-w)` is the SAME number we just handed `setDevice` — so it cannot lag by construction, and with `.canvas`'s exact width the frame is that number too. It runs AFTER `setDevice` on purpose: GrapesJS emits `change:device` synchronously from that call, so the stale write has already happened by the time this line executes.
		if (headEl) headEl.style.width = '';
	}

	function refreshUndo(): void {
		const um = editor?.UndoManager;
		canUndo = !!um?.hasUndo?.();
		canRedo = !!um?.hasRedo?.();
	}

	// EVERY route to an undo goes through create.ts's helpers, never `editor.UndoManager.undo()` — that is the door GrapesJS gates on `em.isEditing()`, which a block drop leaves set (see create.ts → UNDO). The document TemplateEditor calls it directly and can, because nothing there activates an RTE on drop; copying that here would give a button that silently does nothing in exactly the state the author reaches for it, and — worse — one that behaves differently from ctrl+z.
	async function doUndo(): Promise<void> {
		if (!editor) return;
		await undoEmailEdit(editor);
		refreshUndo();
	}

	async function doRedo(): Promise<void> {
		if (!editor) return;
		await redoEmailEdit(editor);
		refreshUndo();
	}

	// Build the history when the menu opens — committing any live text edit FIRST, so the list cannot be one entry short of what a click then undoes.
	async function openUndoMenu(): Promise<void> {
		await endTextEditing(editor);
		undoList = undoHistory(editor);
		undoHover = -1;
		refreshUndo();
	}

	async function undoTo(i: number): Promise<void> {
		undoOpen = false;
		await undoEmailEdits(editor, i + 1);
		refreshUndo();
	}

	// Open one palette category and shut the rest, through GrapesJS's OWN collapse state rather than a copy of it: the panel renders from these models, so driving them is what keeps the rail's active segment and the group actually showing from disagreeing.
	function openBlockCategory(label: string): void {
		const cats = editor?.BlockManager?.getCategories?.();
		const models = (cats?.models ?? cats ?? []) as {
			getLabel?: () => string;
			set?: (key: string, value: unknown) => void;
		}[];
		for (const category of models) category.set?.('open', category.getLabel?.() === label);
	}

	// THE `{{` PICKER'S BRIDGE. Typing `{{` in the canvas opens this template's own merge fields at the caret. The menu itself lives in the canvas script (`gjs-email-jinja-hl.js`), because the caret only exists in there; this half owns the two things the frame cannot know — the vocabulary, and each field's markup (see `fieldMarkup`).
	function frameWindow(): Window | null {
		const frame = (editor?.Canvas?.getFrameEl?.() ??
			editor?.getContainer?.()?.querySelector?.('iframe.gjs-frame')) as
			HTMLIFrameElement | null | undefined;
		return frame?.contentWindow ?? null;
	}

	function publishPickerFields(): void {
		// Read-only means nothing may be inserted, so the picker gets an EMPTY vocabulary rather than a menu that opens and then refuses — it stays inert on an empty list.
		const groups = readOnly
			? []
			: menuGroups.map((group) => ({
					group: group.group,
					fields: group.fields.map((field) => ({
						label: field.label,
						token: field.token,
						html: fieldMarkup(field.token)
					}))
				}));
		frameWindow()?.postMessage({ type: 'gjs-email-fields', groups }, window.location.origin);
	}

	// Re-sent when the vocabulary changes — `fields` is a prop and `code` decides the default set, so the picker must not be left offering the previous template's tokens. Guarded on `ready`: before that there is no frame, and `onMount` does the first send.
	$effect(() => {
		const groups = menuGroups;
		if (!ready || groups.length === 0) return;
		publishPickerFields();
	});

	/** Tell GrapesJS to re-measure the canvas whenever the geometry round it moves.
	 *
	 *  Its highlighter, badge, toolbar and resizer are drawn from a CACHED canvas offset, so a window
	 *  resize, the app sidebar collapsing, or the code view opening leaves every marker where the canvas
	 *  used to be — the exact case `Editor.refresh`'s own docstring names. Coalesced to one call per
	 *  frame: a ResizeObserver fires per observed box per frame, and refreshing three times changes
	 *  nothing but costs three reflows.
	 *
	 *  The COLUMN's scroll is watched too. GrapesJS re-measures on the FRAME's scroll, which is a
	 *  different scroller: the frame is sized to its content and the column is what actually moves.
	 */
	$effect(() => {
		const workspace = workspaceEl;
		const column = columnEl;
		if (!ready || !workspace) return;
		let queued = 0;
		const refresh = (): void => {
			queued = 0;
			// `tools: true` is the whole point — without it only the deprecated `canvasOffset` is updated
			// and the spots (the markers an author can see) are left alone.
			try {
				editor?.refresh?.({ tools: true });
			} catch {
				// A refresh during teardown is not worth surfacing; the editor is going away.
			}
		};
		const schedule = (): void => {
			if (queued) return;
			queued = requestAnimationFrame(refresh);
		};
		const observer = new ResizeObserver(schedule);
		observer.observe(workspace);
		if (column) observer.observe(column);
		column?.addEventListener('scroll', schedule, { passive: true });
		window.addEventListener('resize', schedule, { passive: true });
		return () => {
			if (queued) cancelAnimationFrame(queued);
			observer.disconnect();
			column?.removeEventListener('scroll', schedule);
			window.removeEventListener('resize', schedule);
		};
	});

	onMount(() => {
		destroyed = false;

		// The frame reports a pick. The insert landed in a contenteditable, so GrapesJS commits it on `rte:disable` — too late for the page's Unsaved hint, and an author who types a field and goes straight for Save would post content without it. `emitNow` reads the canvas as it is now.
		const onFrameMessage = (event: MessageEvent): void => {
			// PINNED TO THE CANVAS FRAME, SOURCE FIRST. `window` hears every message posted at this page, and
			// only the frame this editor created has anything to say here — the frame script's own pin is the
			// half that matters (it guards an `insertHTML`), and this side answers with the field vocabulary
			// and reaches into the canvas by id, neither of which is a stranger's business.
			if (event.source !== frameWindow()) return;
			if (event.origin !== window.location.origin) return;
			const type = (event.data as { type?: string } | null)?.type;
			// The canvas script announcing itself. It asks because it cannot be pushed to reliably: the frame's scripts are evaluated after the editor's own init, so a send from `onMount` arrives before there is a listener and is lost — with no symptom except a picker that never opens.
			if (type === 'gjs-email-fields-request') return publishPickerFields();
			if (type !== 'gjs-email-field-inserted') return;
			// `ok: false` means the frame had no LIVE caret — the normal case for a rail click, because
			// moving focus out of the canvas ends the RTE session and the re-parse detaches the range.
			// It names the component the author was last typing in, so the field goes THERE, through the
			// model (undoable), rather than into a new block at the bottom of the email.
			// Older frame scripts send no `ok`; treat that as the success it was.
			const reply = event.data as { ok?: boolean; componentId?: string };
			const markup = pendingInsert;
			pendingInsert = null;
			if (reply.ok === false && markup) return appendField(markup, reply.componentId);
			emitNow();
		};
		window.addEventListener('message', onFrameMessage);

		(async () => {
			if (!canvasEl || !blocksEl) return;
			const ed = await createEmailEditor({ container: canvasEl, blocksEl, readOnly });
			// Component may have been destroyed while the dynamic import was in flight.
			if (destroyed) {
				ed.destroy();
				return;
			}

			editor = ed;

			// The parent seeds html/css into $state in an effect that has already run by the time this async import resolves, so reading the props here gets the freshest content and sidesteps the mount-order race between the child's onMount and the parent's seed effect.
			loadEmailContent(ed, { html, css });
			// The page cell, BEFORE the baseline is captured — it makes the whole white sheet a `<td>` so a block can be dropped anywhere on it (see `ensurePageCell`). Inside the baseline deliberately: the wrap is not an edit the author made, and marking the template dirty for merely opening it would disable Publish on arrival.
			ensurePageCell(ed);
			// The dirty baseline is what GrapesJS made of the saved content, captured BEFORE the change listeners attach below — so the load itself can never read as an edit.
			lastEmitted = read();
			// AND HANDED TO THE PARENT, which is the half that was missing. The page takes its dirty baseline from the FIRST emission it receives; with no emission for the load, that first one was the author's first real edit — so clicking "Starter layout" on an empty template left `dirty` false: no Unsaved hint, no navigation guard, and Publish enabled against the previously saved body. Emitted directly rather than through `emitNow`, whose dedupe would suppress it (it has just been seeded with this very value).
			onChange(lastEmitted);
			refreshEmpty();
			stopFrameSync = syncFrameHeight(ed);
			// After `loadEmailContent`, so the frame it measures its width from has been laid out once.
			if (headEl) stopHeadMount = syncHeadWidth(ed, headEl);
			stopBrandPreview = previewBrandMasthead(ed, brandLogoUrl, brandName);
			// The pair this canvas is currently painted for, so the effect below can tell a real
			// change from its own first run. Set HERE rather than letting the effect do the first
			// apply: the order in this block is load-bearing (the paint registers listeners on the
			// frame, and an effect would run a flush later, after `canvas:frame:load` has fired).
			appliedBrand = brandKey(brandLogoUrl, brandName);
			styleManager = ed.StyleManager as StyleManagerLike;
			// The palette's categories. A Backbone collection, so `.models`; the fallbacks keep a GrapesJS change of shape from emptying the rail silently rather than loudly.
			const cats = ed.BlockManager?.getCategories?.();
			blockCategories = ((cats?.models ?? cats ?? []) as { getLabel?: () => string }[])
				.map((c) => c.getLabel?.() ?? '')
				.filter(Boolean);
			// One open at a time, matching the rail: GrapesJS ships them all open, which in a narrow flyout is every block at once and no sense of which segment you are looking at.
			openBlockCategory(blockCategories[0] ?? '');

			publishPickerFields();

			// Listeners attach AFTER the initial parse, so the load itself isn't reported as an edit. `update` is GrapesJS's catch-all change signal; the others make component + style edits explicit. `rte:disable` flushes immediately since RTE content only commits on blur.
			ed.on('update', scheduleEmit);
			ed.on('component:update', scheduleEmit);
			ed.on('component:styleUpdate', scheduleEmit);
			ed.on('rte:disable', emitNow);
			const onStructureChange = (): void => {
				refreshEmpty();
				refreshPosition();
			};
			ed.on('component:add', onStructureChange);
			ed.on('component:remove', onStructureChange);
			ed.on('component:selected', refreshSelection);
			// Selecting something IS "I am working in the canvas now" — the page drops its remembered
			// head caret on this, so an insert goes where the author is rather than where they were.
			// NOT a flyout close: `component:selected` is not a user gesture. It fires while the canvas
			// settles — on an empty template it fired repeatedly — and closing on it slammed the flyout
			// shut the instant it opened. The backdrop click is the gesture that dismisses it.
			ed.on('component:selected', () => oncanvasfocus?.());
			ed.on('component:deselected', refreshSelection);
			refreshSelection();
			// The style panel holds no copy of the values — it re-reads the models on `revision`. A style can move from four directions (the panel itself, an undo, an edit in the canvas, and `syncButtonLabelInk` writing the second level of a CTA's ink), so every one of them has to bump it. A cached tree would need invalidating by all four, which is how a panel ends up showing a value the element does not have.
			const bump = (): void => {
				revision += 1;
			};
			// `style:target` AND `style:custom` are not optional. GrapesJS resolves the StyleManager's target on a DEBOUNCED handler (`upAll`, a `debounce(…, 0)` over `component:toggled` — grapes.mjs :67165), so at the moment `component:selected` fires the property models still answer for the PREVIOUS selection — and nothing bumps again afterwards, so bumping on selection alone leaves the panel showing the last block's padding under the new block's name, with an edit then writing it to the new block. `style:target` fires once the target has actually changed (`change:lastTarget`, :67178) and `style:custom` once the properties have been re-read.
			for (const ev of [
				'style:target',
				'style:custom',
				'update',
				'undo',
				'redo',
				'component:update',
				'component:styleUpdate',
				'component:add',
				'component:remove',
				'rte:disable'
			])
				ed.on(ev, bump);
			// Undo/redo enablement. `update` alone is not enough — it does NOT fire for a plain append (measured), so the buttons stay dead through the very first block drop. `undo`/`redo` are the UndoManager's own events, re-emitted on the editor (grapes.mjs :54142), and are what re-arms Redo the moment something has been undone. `load` is in the list because parsing the stored template DOES put entries on the stack and GrapesJS then throws them away itself — `EditorView` runs `UndoManager.clear()` on `change:ready` and only then triggers `load` (grapes.mjs :61651-61657). The clear fires no event, so a reading taken before it leaves Undo enabled for ever on a template nobody has touched. (That clear is also why ctrl+z on a freshly opened template cannot blank it.)
			for (const ev of [
				'load',
				'update',
				'undo',
				'redo',
				'component:add',
				'component:remove',
				'component:update',
				'component:styleUpdate',
				'rte:disable'
			])
				ed.on(ev, refreshUndo);
			refreshUndo();

			// THE RIGHT-CLICK, listened for INSIDE the frame — the only place it happens, since a canvas event never leaves the iframe. Re-attached on every frame load rather than once, because GrapesJS rebuilds the frame document when the canvas is re-created and a listener bound to the old one leaves the browser's own menu appearing with no clue why. `capture: true` so it runs before anything in the frame can swallow it — the canvas script (`gjs-email-jinja-hl.js`) is a stranger to this file and must not be able to.
			const bindCanvasMenu = (): void => {
				const doc = ed.Canvas?.getDocument?.();
				if (!doc || doc === canvasMenuDoc) return;
				canvasMenuDoc?.removeEventListener('contextmenu', openCanvasMenu, true);
				doc.addEventListener('contextmenu', openCanvasMenu, true);
				canvasMenuDoc = doc;
			};
			ed.on('canvas:frame:load', bindCanvasMenu);
			// GrapesJS runs `assetManager: { custom: true }`, so it renders no modal of its own and asks us instead — double-clicking an image lands here. Without this the interaction is simply dead.
			// A DOUBLE-CLICK ON AN IMAGE IS ANSWERED BY THE RAIL, which holds the source, the firm's
			// library and the upload. Two things have to happen: shut GrapesJS's own modal
			// (`assetManager: { custom: true }` stops it RENDERING an asset manager, not opening the shell
			// that would have held one, so an empty "Select Image" dialog appeared in unstyled chrome) and
			// OPEN THE RAIL — closed, the double-click otherwise did visibly nothing at all.
			ed.on('asset:custom', (props: { open?: boolean }) => {
				if (!props?.open) return;
				ed.Modal?.close?.();
				if (!codeOpen) railOpen = true;
			});
			ed.on('load', bindCanvasMenu);
			bindCanvasMenu();

			// Dev-only handle for headless verification / debugging in the browser console. `import.meta` is cast because pkgs/ui's tsconfig doesn't pull Vite's client ambient types.
			if ((import.meta as { env?: { DEV?: boolean } }).env?.DEV)
				(window as unknown as Record<string, unknown>).__gjsEmail = ed;
			ready = true;
		})().catch((err) => {
			// A rejected init (usually the grapesjs dynamic import) must NOT vanish into an unhandled promise — that leaves the workspace blank with no clue. Surface it + log it.
			if (destroyed) return;
			mountError = err instanceof Error ? err.message : String(err);
			console.error('[EmailEditor] failed to initialise', err);
		});

		return () => {
			destroyed = true;
			window.removeEventListener('message', onFrameMessage);
			// The frame document outlives this component only if the editor failed to destroy, but the listener is on a document THIS file does not own — so it comes off explicitly rather than being left to the frame's teardown.
			canvasMenuDoc?.removeEventListener('contextmenu', openCanvasMenu, true);
			canvasMenuDoc = null;
			if (debounceTimer) clearTimeout(debounceTimer);
			stopFrameSync?.();
			stopFrameSync = null;
			stopHeadMount?.();
			stopHeadMount = null;
			stopBrandPreview?.();
			stopBrandPreview = null;
			// Undo owns a keydown listener on the top document (GrapesJS's keymaps are unusable for it — any editor's `destroy()` wipes the page-global keymaster map; see create.ts → UNDO). It must go BEFORE destroy, or a keystroke arriving mid-teardown undoes into a gutted editor.
			editor?.__disposeUndoShortcuts?.();
			if (editor) {
				editor.destroy();
				editor = null;
			}
		};
	});
</script>

<div class="email-editor bg-card flex h-full min-h-0 flex-col overflow-hidden">
	<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-3 py-2">
		<!-- Undo / redo, in the document TemplateEditor's own arrangement. Absent entirely when read-only: `createEmailEditor` does not install the undo path for an `email_templates:read` principal (mutually exclusive branches, see create.ts), so rendering them disabled would advertise a capability that is not merely switched off but not built. The titles carry the chords, because the keystroke stays the fast path and this is what teaches it. -->
		{#if !readOnly}
			<div class="flex items-center">
				<div class="flex items-center">
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label="Undo"
						title="Undo (Ctrl+Z)"
						disabled={!ready || !canUndo}
						onclick={doUndo}
					>
						<Undo2Icon class="size-4" />
					</Button>
					<DropdownMenu.Root bind:open={undoOpen} onOpenChange={(o) => o && openUndoMenu()}>
						<DropdownMenu.Trigger
							class="text-muted-foreground hover:bg-muted hover:text-foreground -ml-0.5 flex h-6 items-center rounded px-0.5 disabled:pointer-events-none disabled:opacity-50"
							aria-label="Undo history"
							title="Undo history"
							disabled={!ready || !canUndo}
						>
							<ChevronDownIcon class="size-3" />
						</DropdownMenu.Trigger>
						<DropdownMenu.Content align="start" class="w-56 p-0">
							{#if undoList.length === 0}
								<p class="text-muted-foreground p-3 text-xs">Nothing to undo.</p>
							{:else}
								<div
									class="max-h-72 overflow-y-auto py-1"
									role="group"
									onmouseleave={() => (undoHover = -1)}
								>
									{#each undoList as entry, i (i)}
										<DropdownMenu.Item
											class={[
												'gap-2 rounded-none px-3 py-1.5 text-xs',
												i <= undoHover && 'bg-muted'
											]}
											onmouseenter={() => (undoHover = i)}
											onSelect={() => undoTo(i)}
										>
											<Undo2Icon class="text-muted-foreground size-3 shrink-0" />
											<span class="truncate">{entry.label}</span>
										</DropdownMenu.Item>
									{/each}
								</div>
								<div class="text-muted-foreground border-t px-3 py-1.5 text-[11px]">
									{undoHover >= 0
										? `Undo ${undoHover + 1} action${undoHover === 0 ? '' : 's'}`
										: `${undoList.length} action${undoList.length === 1 ? '' : 's'} available`}
								</div>
							{/if}
						</DropdownMenu.Content>
					</DropdownMenu.Root>
				</div>
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label="Redo"
					title="Redo (Ctrl+Shift+Z)"
					disabled={!ready || !canRedo}
					onclick={doRedo}
				>
					<Redo2Icon class="size-4" />
				</Button>
				<div class="bg-border mx-1.5 h-4 w-px"></div>
			</div>
		{/if}
		{#if !readOnly && ready && canvasEmpty && defaultBody}
			<Button type="button" variant="outline" size="sm" onclick={loadDefault}>
				<SparklesIcon class="mr-1.5 size-4" /> Load default
			</Button>
		{/if}
		{#if !readOnly && ready && canvasEmpty}
			<Button type="button" variant="outline" size="sm" onclick={insertStarter}>
				<LayoutTemplateIcon class="mr-1.5 size-4" /> Starter layout
			</Button>
		{/if}
		<!-- Which width the canvas is drawn at. THE mode toggle, so it is `Segmented` and not two Buttons swapping `variant` — the hand-rolled pill group that component exists to absorb (docs/design-language.md), which also had no `tablist` semantics and no arrow-key movement. The values are GrapesJS's own device names, which is why `Mobile portrait` is labelled `Mobile`. IT PREVIEWS A WIDTH; IT DOES NOT SELECT A STYLE TARGET, and it is named for that: every other WYSIWYG's device switch means "style this breakpoint", and this one cannot, because the editor runs `avoidInlineStyle: false` so a style written in Mobile lands INLINE on the element and applies at every width. Nor is it fixable — a `@media` rule would be the mechanism, and `css_inline` runs `keep_at_rules=False`, so an at-rule never reaches an inbox. -->
		<Segmented
			class="ml-auto"
			size="sm"
			label="Preview width"
			disabled={!ready}
			value={device}
			options={[
				{ value: 'Desktop', label: 'Desktop', icon: MonitorIcon },
				{ value: 'Mobile portrait', label: 'Mobile', icon: SmartphoneIcon }
			]}
			onValueChange={(v) => setDevice(v === 'Mobile portrait' ? 'Mobile portrait' : 'Desktop')}
		/>
	</div>

	{#if mountError}
		<p class="text-destructive border-destructive/30 bg-destructive/10 border-b px-4 py-2 text-sm">
			The editor couldn't load ({mountError}). Try reloading the page; if it persists, the browser
			console has the details.
		</p>
	{/if}

	<!-- The element GrapesJS's BlockManager renders into. A snippet so the RAIL owns where it sits while this component owns the binding — and so it is created exactly ONCE: the manager mounts into it at init and there is no second mount. -->
	{#snippet grapesPalette()}
		<div bind:this={blocksEl}></div>
	{/snippet}

	<!-- Workspace: icon rail · canvas (the email's head, then the frame) · style panel. `code-open` is what makes room for the code view. A CLASS driving a stylesheet variable rather than an inline `style:` binding, so both padding sums stay declared beside each other where a mismatch between them is visible (`.canvas-column` and `.loading-canvas` do the same sum). -->
	<div
		bind:this={workspaceEl}
		class="workspace"
		class:rail-hidden={!railOpen}
		class:code-open={codeOpen}
		style:--device-w={deviceWidth(device)}
	>
		<!-- The rail FLOATS over the canvas rather than holding a column of the grid — see `.rail-right` for the posture and for why it is positioned against `.workspace` and not the column. `oncode` is passed even when READ-ONLY: reading and copying the HTML of a template you may only read is a real thing to want (the same argument `openCanvasMenu` makes about the browser's own copy menu). What read-only withholds is the unlock, `onapply` below. -->
		<BlocksRail
			bind:open={railFlyout}
			mergeFields={menuGroups}
			{onreference}
			disabled={readOnly || !ready}
			oninsertField={readOnly ? undefined : insertToken}
			paletteMount={grapesPalette}
			categories={blockCategories}
			onselectcategory={openBlockCategory}
			{codeOpen}
			codeDisabled={!ready}
			oncode={toggleCode}
		/>

		<!-- THE HTML CODE VIEW, floating ON the canvas — a third card in the same posture as the two rails. See the `codeOpen` note for why this one displaces the email and the style panel must not. -->
		{#if codeOpen}
			<CodeView
				html={codeHtml}
				shape={codeShape}
				onshapechange={(s) => (codeShape = s)}
				onapply={readOnly ? undefined : applyCode}
				onclose={toggleCode}
			/>
		{/if}

		<!-- The canvas column, and the SCROLLER — it owns the backdrop, the gutter and the scrollbar, so the head is inside the same scrolling, centred space as the card rather than behind it (see `.canvas-column`). `syncFrameHeight` sizes the mount to the email's own height, which is what gives this column something to scroll. `--device-w` is what makes the toggle a DEVICE switch rather than a canvas-width switch: the head is a snippet of ours sitting outside GrapesJS's frame, so nothing GrapesJS does can narrow it. A `style:` DIRECTIVE, never a `style=""` string — a string attribute is rewritten wholesale on update and would wipe any other custom property set on this element (the standing gotcha the client portal's grid records at length). -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<div bind:this={columnEl} class="canvas-column" onclick={deselectOnBackdrop}>
			{#if documentHeader}
				<div bind:this={headEl} class="gjs-email-head">{@render documentHeader()}</div>
			{/if}
			<div bind:this={canvasEl} class="canvas"></div>
		</div>

		{#if railOpen}
			<!-- `data-field-tone="filled"` is how every field INSIDE this panel becomes a filled box rather than an outlined one. Declared once on the panel rather than per control — see `field-shell.ts::FIELD_TONE_FILLED` for why it is an ancestor attribute and not a prop. -->
			<aside class="rail rail-right" data-field-tone="filled">
				{#if selectedName}
					<button type="button" class="rail-back" onclick={backToPageStyle}>
						<ArrowLeftIcon class="size-3.5 shrink-0" />
						<span>Page style</span>
					</button>
				{/if}
				<div class="rail-head">
					{#if selectedName}
						<PaintbrushIcon class="text-muted-foreground size-3.5 shrink-0" />
					{:else}
						<FileTextIcon class="text-muted-foreground size-3.5 shrink-0" />
					{/if}
					<span class="rail-head-title">{selectedName ? 'Style' : 'Page style'}</span>
					<div class="rail-head-actions">
						{#if !readOnly && position}
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move to top"
								title="Move to the top of its container"
								disabled={!canMove('top', position)}
								onclick={() => moveSelection('top')}
							>
								<ChevronsUpIcon class="size-4" />
							</Button>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move up"
								title="Move up"
								disabled={!canMove('up', position)}
								onclick={() => moveSelection('up')}
							>
								<ArrowUpIcon class="size-4" />
							</Button>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move down"
								title="Move down"
								disabled={!canMove('down', position)}
								onclick={() => moveSelection('down')}
							>
								<ArrowDownIcon class="size-4" />
							</Button>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move to bottom"
								title="Move to the bottom of its container"
								disabled={!canMove('bottom', position)}
								onclick={() => moveSelection('bottom')}
							>
								<ChevronsDownIcon class="size-4" />
							</Button>
							<div class="rail-head-divider"></div>
						{/if}
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label="Hide the style panel"
							title="Hide the style panel"
							onclick={() => (railOpen = false)}
						>
							<PanelRightIcon class="size-4" />
						</Button>
					</div>
				</div>
				<div class="rail-section">
					{#if !readOnly && position}
						{#if repeatChoices.length > 0}
							<!-- THE ONLY PLACE A REPEAT IS MARKED. Nothing is drawn on the canvas: every treatment
							     tried there was eclipsed by the element itself. -->
							<InspectorSector label="Repeat" bind:open={repeatOpen} set={!!repeat}>
								{#snippet mark()}
									<RepeatIcon class="size-3.5" aria-label="This row repeats" />
								{/snippet}
								<div class="flex flex-col gap-1 px-2 pt-1 pb-2">
									<PropertyRow
										label="How often"
										for="et-repeat"
										align="start"
										set={!!repeat}
										hint={repeat
											? `Written once per item of ${repeat.field}, with each one available as {{ ${repeat.alias} }}.`
											: 'Write this once per item of a list the send provides.'}
										onclear={repeat ? () => applyRepeat('') : undefined}
									>
										<!-- NO PLACEHOLDER. Not repeating is a real answer, not an absent one, so the empty option carries the words ("Once only") and the control always shows the value it has. A placeholder of the same string could never render, and would have made "chosen" and "not chosen" the same picture if it did. -->
										<SelectMenu
											id="et-repeat"
											size="sm"
											value={repeat?.field ?? ''}
											items={[
												{ value: '', label: 'Once only' },
												...repeatChoices.map((f) => ({
													value: f.field,
													label: `For each ${f.label}`
												}))
											]}
											onValueChange={applyRepeat}
										/>
									</PropertyRow>
								</div>
							</InspectorSector>
						{/if}
						<!-- WHETHER this block appears — above Style, because whether an element is in the email at all is a bigger question than what it looks like. Guarded on `position` rather than on a selection, because the wrapper itself has no parent and cannot carry a condition. -->
						<!-- THE ONLY PLACE A CONDITION IS MARKED — see the note on Repeat above. -->
						<InspectorSector label="Visibility" bind:open={visibilityOpen} set={!!visibility}>
							{#snippet mark()}
								<SplitIcon class="size-3.5" aria-label="This block is conditional" />
							{/snippet}
							<VisibilityEditor
								{visibility}
								fields={conditionFields}
								idPrefix="et-vis"
								onchange={applyVisibility}
							/>
						</InspectorSector>
					{/if}

					{#if !readOnly && field}
						<InspectorSector label="Field" set={!!field.filter || !!field.fallback}>
							<div class="flex flex-col gap-1 px-2 pt-1 pb-2">
								<PropertyRow label="Field" set>
									<code class="text-muted-foreground truncate font-mono text-[0.7rem]"
										>{field.field}</code
									>
								</PropertyRow>
								<PropertyRow label="Format" for="et-field-filter" set={!!field.filter}>
									<SelectMenu
										id="et-field-filter"
										size="sm"
										value={field.filter}
										placeholder={NO_FILTER_LABEL}
										items={[
											{ value: NO_FILTER, label: NO_FILTER_LABEL },
											...FIELD_FILTERS.map((f) => ({ value: f.value, label: f.label }))
										]}
										onValueChange={applyFieldFilter}
									/>
								</PropertyRow>
								<PropertyRow
									label="If empty"
									for="et-field-fallback"
									align="start"
									set={!!field.fallback}
									hint={describeFallback(field.fallback)}
									onclear={field.fallback ? () => applyFieldFallback('') : undefined}
								>
									<Input
										id="et-field-fallback"
										class="h-8"
										value={field.fallback}
										placeholder="Nothing"
										spellcheck="false"
										oninput={(e) => applyFieldFallback(e.currentTarget.value)}
									/>
								</PropertyRow>
								<code
									class="text-muted-foreground bg-muted mt-1 overflow-x-auto rounded px-1.5 py-1 font-mono text-[0.65rem]"
								>
									{fieldToken(field.field, field.filter, field.fallback)}
								</code>
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									{describeFilter(field.filter)}
								</p>
							</div>
						</InspectorSector>
					{/if}

					{#if !readOnly && (linkTarget || imageTarget)}
						<InspectorSector
							label={linkTarget ? 'Link' : isBrandLogo(imageTarget) ? 'Brand logo' : 'Image'}
							bind:open={inspectorOpen}
							set={linkTarget
								? !!linkHref
								: isBrandLogo(imageTarget)
									? !!imageFallback?.text
									: !!imageAlt}
						>
							<div class="flex flex-col gap-3 px-2 pt-1 pb-2">
								{#if linkTarget}
									<!-- THE FIELD ALONE. A "Destination" label under a sector already called Link named
									     the same thing twice, and the placeholder says a merge field works here — a
									     line of prose under every link said it again. NO "open in a new tab": a mail
									     client opens a link in a browser whichever way `target` is written, so the
									     control decided nothing an author could see. -->
									<Input
										id="et-href"
										class="h-8"
										value={linkHref}
										placeholder="https://…  or  &lbrace;&lbrace; portal_url &rbrace;&rbrace;"
										oninput={(e) => applyHref(e.currentTarget.value)}
									/>
								{/if}
								{#if imageTarget && isBrandLogo(imageTarget)}
									<!-- MASTHEAD ONLY. Source / upload / alt are the content-image controls; the firm
									     logo is still wordmark-led while brand image storage is unfinished, so the rail
									     says that and only edits the wordmark text. -->
									<p class="text-muted-foreground text-xs leading-snug">
										Uses the firm wordmark for now — the logo image path is not editable here yet.
									</p>
									{#if imageFallback}
										<PropertyRow
											label="Wordmark"
											for="et-img-fallback"
											layout="stack"
											set={!!imageFallback.text}
										>
											<Input
												id="et-img-fallback"
												class="h-8"
												value={imageFallback.text}
												placeholder={'{{ brand.name }}'}
												spellcheck="false"
												oninput={(e) => applyImageFallback(e.currentTarget.value)}
											/>
										</PropertyRow>
									{/if}
								{:else if imageTarget}
									<!-- Content images only. "If no image" is a masthead concern and stays off this path. -->
									<PropertyRow
										label="Source"
										for="et-img-src"
										layout="stack"
										set={isUsableInEmail(imageSrc)}
									>
										<ImageSource
											src={imageSrc}
											libraryUrl={imagesUrl}
											onpick={(url) => applyImageSrc(url)}
											onchange={(url) => applyImageSrc(url)}
										/>
									</PropertyRow>
									<PropertyRow
										label="Alternative text"
										for="et-alt"
										layout="stack"
										set={!!imageAlt}
									>
										<Input
											id="et-alt"
											class="h-8"
											value={imageAlt}
											placeholder="Describe the image"
											oninput={(e) => applyAlt(e.currentTarget.value)}
										/>
									</PropertyRow>
								{/if}
							</div>
						</InspectorSector>
					{/if}

					{#if selectedName && device !== 'Desktop'}
						<!-- SAID HERE because this is where the mistake would be made: an author who narrows the canvas and then restyles reasonably believes they are styling the narrow case, and there is no width at which they could be (see the device toggle). Only while the preview is narrowed — a permanent notice would be furniture nobody reads. -->
						<p
							class="text-muted-foreground border-b px-3 py-2 text-[0.7rem] leading-snug"
							role="note"
						>
							Styles apply at every width — this is a preview size, not a separate design.
						</p>
					{/if}
					{#if selectedName}
						<StylePanel
							{styleManager}
							{revision}
							{brandColours}
							disabled={readOnly}
							onchange={() => {
								// The write already happened, through the property model. Bump so every control re-reads (a composite's shorthand changes what its siblings report), and emit now rather than on the debounce so the page's dirty flag is immediate.
								revision += 1;
								emitNow();
							}}
						/>
					{:else if !readOnly}
						<!-- THE IDLE STATE IS THE PAGE, not a sentence telling the author to go and click something. The page is deliberately unselectable (`protectPage`), so its background and inset are unreachable through the style rail — and this is where an author looks for them, before anything is selected. -->
						<PageStyle
							background={pageStyle.background}
							padding={pageStyle.padding}
							{brandColours}
							onchange={(patch) => {
								setPageStyle(editor, patch);
								refreshPageStyle();
								emitNow();
							}}
							oninput={(patch) => {
								// Untracked, and NO `emitNow`: a shade being dragged past is not a version of the
								// email worth saving, and the committed write above emits.
								setPageStyle(editor, patch, { partial: true });
								refreshPageStyle();
							}}
						/>
					{/if}
				</div>
			</aside>
		{:else if !codeOpen}
			<!-- Shut, the rail is one arrow on the workspace's right edge, positioned against `.workspace` like the blocks rail (the canvas column scrolls, so a child of it would slide away with the email). NOT WHILE THE CODE VIEW IS OPEN: the code pane spans to this tab's own inset, so the tab would sit on top of it offering to reopen a panel that would then cover the code — and the `</>` button in the rail already leaves code mode, restoring the panel to exactly the state it was in (`toggleCode`). -->
			<button
				type="button"
				class="rail-reopen"
				aria-label="Show the style panel"
				title="Show the style panel"
				onclick={() => (railOpen = true)}
			>
				<ChevronLeftIcon class="size-4" />
			</button>
		{/if}

		<!-- THE RIGHT-CLICK MENU. Inside `.workspace` because the trigger is absolutely positioned and needs a positioned ancestor. It renders nothing until opened, and wraps nothing round the canvas column, whose layout is measured (see `canvas-menu.svelte` on the zero-size span). -->
		{#if !readOnly}
			<CanvasMenu
				bind:open={menuOpen}
				bind:triggerRef={menuTrigger}
				subject={menuSubject}
				mergeFields={menuGroups}
				onaction={runMenuAction}
				oninsertField={insertToken}
			/>
		{/if}

		<!-- LOADING. The editor is a dynamic import plus a GrapesJS init plus a frame whose own scripts evaluate after it, so for the better part of a second the author would watch the workspace assemble itself; disabling the controls (which `!ready` already does) does not fix that, so the whole workspace is covered until it is finished. A SKELETON of the layout rather than a spinner, because the shape is knowable and drawing it means NOTHING MOVES AT THE REVEAL — the sheet lands exactly where its placeholder was, at the same device width (`--device-w`). Not shown once `mountError` is set: a skeleton that never resolves is worse than the error. -->
		{#if !ready && !mountError}
			<div class="loading" aria-hidden="true">
				<div class="loading-canvas">
					<Skeleton class="h-8 w-full rounded-md" />
					<div class="loading-sheet">
						<Skeleton class="h-9 w-1/2 rounded" />
						<Skeleton class="h-3 w-full rounded" />
						<Skeleton class="h-3 w-11/12 rounded" />
						<Skeleton class="h-3 w-4/5 rounded" />
						<Skeleton class="mt-2 h-9 w-32 rounded" />
					</div>
				</div>
				{#if railOpen}
					<div class="loading-rail">
						<Skeleton class="h-4 w-20 rounded" />
						<Skeleton class="h-8 w-full rounded" />
						<Skeleton class="h-8 w-full rounded" />
						<Skeleton class="h-4 w-24 rounded" />
						<Skeleton class="h-8 w-full rounded" />
					</div>
				{/if}
			</div>
			<!-- Said once, out of band, for a screen reader: the visual skeleton is `aria-hidden`, so without this the editor announces nothing at all while it loads. -->
			<p class="sr-only" role="status">Loading the email editor…</p>
		{/if}
	</div>
</div>

<style>
	/* ONE COLUMN — the canvas — with both rails FLOATING over it (see `.rail-right`). The workspace FILLS the height its parent gives it (flex:1 inside the flex-column `.email-editor`, which the edit page mounts inside `h-[calc(100svh-var(--topbar-h,4rem))]`) — no viewport magic number, so the editor is flush to the bottom in every shell (the platform view and the admin SaaSShell, whose top bar a hardcoded 100svh-Nrem could never model). `min-height:0` lets it shrink so the canvas column keeps the only scrollbar. `position: relative` is load-bearing, not tidiness: the rails are positioned against THIS box and not against the column, because the column scrolls and a rail inside it would slide away with the email. `.rail-hidden` stays as a hook for anything that wants to know, but drives NO layout — the panel's presence is absent from the column's arithmetic entirely, which is what keeps the email both still and centred. */
	.workspace {
		/* The canvas column's own scrollbar takes width off its content box, and a `100%` in the gutter arithmetic cannot see it — a percentage there resolves against this element. Left unaccounted for, the content box comes out ~10px short of the email and the head and the frame overflow by two different rules. So the allowance is NAMED and subtracted — declared HERE because it is what `--code-w` yields by, and a sibling cannot inherit from a sibling. 12px is the widest the thin, styled scrollbar gets on the engines this runs on. NOTHING IS RESERVED FOR THE STYLE PANEL: there is no `--rail-gutter`, and WHAT MUST NOT COME BACK is a gutter that depends on `railOpen` (pinned in `rail.test.ts`) — a gutter that changes with the panel slides the centred email sideways on every toggle, and a constant reservation holds it still but parks it permanently ~140px left of the middle behind a held gap that is empty whenever the panel is away. Reserving nothing satisfies both rules, and a 320px panel does not overlap a 640px sheet in an ordinary workspace anyway. */
		--scrollbar-allowance: 12px;
		/* THE CHROME'S OWN SURFACE — the blocks pill, the style panel and the code view. It must NOT be `var(--card)`, the token the EMAIL SHEET paints in, or a panel resting on the canvas reads as part of the document rather than as a tool on top of it: three distinguishable surfaces, in order sheet (`--card`) · chrome (this) · backdrop (`--muted`). A MIX TOWARD `--foreground` is what makes one declaration correct in BOTH themes, and is why there is no `@media` branch — in light the sheet is the lightest thing (`--card` L 1.0, `--muted` L 0.965) so the mix darkens, in dark the sheet is the DARKEST (0.22 against 0.27) so the same mix lightens. 3% is arithmetic, not taste: light lands at L≈0.977, dark at L≈0.242, and much more reaches the backdrop in light mode and collapses the hierarchy — the window between the two tokens is only 0.035 wide. `in oklab`, NOT `oklch`: `theme.css` documents at length that hue interpolation drags a mixed neutral toward blue. */
		--email-chrome: color-mix(in oklab, var(--card) 97%, var(--foreground));
		/* THE CODE VIEW'S OWN WIDTH AND GUTTER — the one gutter that IS dynamic; see the `codeOpen` note in the script for why a code view displaces the email where a style panel must not. The width YIELDS, which is what makes "both fully visible" true rather than aspirational: with the insets symmetric (see `.canvas-column`) the email is centred in what is left, so the pane may take everything except the 640px email, the two 68px insets, 20px of air beside the pane and the scrollbar — hence `100% - 792px - allowance`. The 900px cap only stops the pane growing once a line fits; what forces sideways scrolling in email markup is one very long `style="…"` attribute on an opening tag, so width here is not comfort but whether the line can be read at all — do not lower it without measuring that. Above the 300px floor the email is never covered; below it the workspace is too narrow for both and the email overflows right, the column scrolling to it rather than anything shrinking. The gutter is the pane's width plus its air, so a change to one is a visible mismatch here rather than a silent overlap: `.canvas-column`'s left inset is `68px + gutter` and the pane ends at `64px + width`, which leaves 20px between them. */
		--code-w: clamp(300px, calc(100% - 792px - var(--scrollbar-allowance)), 900px);
		--code-gutter: 0px;
		position: relative;
		display: flex;
		flex: 1;
		min-height: 0;
	}
	.workspace.code-open {
		--code-gutter: calc(var(--code-w) + 16px);
	}
	.rail-back {
		display: flex;
		flex: none;
		align-items: center;
		gap: 6px;
		width: 100%;
		padding: 6px 10px;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		text-align: left;
		border-bottom: 1px solid var(--border);
		cursor: pointer;
		transition: color 0.1s;
	}
	.rail-back:hover {
		color: var(--foreground);
	}
	.rail {
		display: flex;
		flex-direction: column;
		min-height: 0;
		overflow-y: auto;
		/* THE GUTTER IS ALWAYS RESERVED. Without it the scrollbar's arrival takes ~15px off the content
		   width, which is enough to wrap a label-and-control row onto two lines — so opening a section
		   reflowed the sections above it. Reserving the space costs 15px once and nothing moves. */
		scrollbar-gutter: stable;
		background: var(--email-chrome);
	}
	/* A CARD FLOATING OVER THE CANVAS, not a column welded to the right edge — the same posture the blocks rail takes on the left, and for the same reason: a panel resting ON the workspace reads as a tool you can put away, where a strip attached to the window edge reads as part of the window. Positioned against `.workspace`, NOT the canvas column: the column is the scroller, so a child of it would slide away with the email. An INSET `outline` rather than a `border`, plus the product's own elevation token: an inset outline costs no layout, which a border on an absolutely positioned box would have to be sized around. No `overflow: hidden` — it would override `.rail`'s own `overflow-y: auto` and kill the scroll. */
	.rail-right {
		position: absolute;
		top: 12px;
		right: 12px;
		bottom: 12px;
		z-index: 20;
		/* The panel reserves no space in the canvas column's arithmetic, so this width is a statement about the panel alone and changing it moves nothing else — except the label/control ratio inside every `PropertyRow`, whose label column is a fixed 6.5rem, so the wider the panel the smaller its share. Measured at 320px: 104px of label to ~180px of control, and a two-word label ('Corner radius', 'Border collapse') still sets on one line. NARROWING THIS BREAKS THAT. `.loading-rail` must carry the same number or the reveal jumps. */
		width: 320px;
		border-radius: 12px;
		box-shadow: var(--elev-2);
		outline: 1px solid color-mix(in oklab, var(--foreground) 10%, transparent);
		outline-offset: -1px;
		gap: 0;
		padding-bottom: 8px;
	}
	.rail-head {
		display: flex;
		flex: none;
		align-items: center;
		gap: 6px;
		padding: 7px 6px 7px 10px;
		border-bottom: 1px solid var(--border);
	}
	.rail-head-title {
		flex: 1;
		min-width: 0;
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--foreground);
	}
	.rail-head-actions {
		display: flex;
		flex: none;
		align-items: center;
		gap: 1px;
	}
	.rail-head-divider {
		width: 1px;
		height: 16px;
		margin: 0 4px;
		background: var(--border);
	}
	.rail-reopen {
		position: absolute;
		top: 12px;
		right: 0;
		z-index: 20;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 32px;
		color: var(--muted-foreground);
		background: var(--email-chrome);
		border: 1px solid var(--border);
		border-right: none;
		border-radius: 6px 0 0 6px;
		cursor: pointer;
		transition: color 0.1s;
	}
	.rail-reopen:hover {
		color: var(--foreground);
	}
	/* `flex: none` so the section's BOX is its content's height. As a shrinkable flex item (`0 1 auto` + `min-height: 0`) it collapsed to the rail's height while its content overflowed it — measured at 712px of box for a 2,200px sector list — which confines anything positioned against it to the wrong containing block. */
	.rail-section {
		display: flex;
		flex-direction: column;
		flex: none;
	}
	/* THE SCROLLER. The backdrop, the gutter and the scrollbar live here and NOT on `.gjs-cv-canvas` (see the note in create.ts): the head has to scroll and be centred with the card, and inside that element it could do neither — everything in there is absolutely positioned, and the full-size frames layer paints over any in-flow sibling. The slim overlay scrollbar is the document TemplateEditor's exact treatment, and reserves no width, so it cannot appear and shift the canvas sideways. */
	.canvas-column {
		/* `scrollbar-gutter: stable` so the scrollbar's width is a CONSTANT rather than something that appears the moment the email grows tall enough to scroll — the padding arithmetic below subtracts it (see `--scrollbar-allowance` on `.workspace`), and an allowance that only sometimes applies would move the email sideways as the author typed. */
		scrollbar-gutter: stable;
		display: flex;
		/* FILLS THE WORKSPACE. As a flex item it must be said explicitly: `flex: 0 1 auto` sizes it by its content, so the grey backdrop stops short of the right edge and the style panel floats over WHITE instead of over the canvas — a panel resting on nothing. */
		flex: 1;
		flex-direction: column;
		/* What puts the head on the card's centre line. It is here rather than a `margin: 0 auto` in the injected GrapesJS sheet because THAT sheet is created once and guarded by its element id, so a hot reload leaves the old copy in place and layout declared there goes stale under the edits being made to it. The component's own stylesheet always reloads. */
		align-items: center;
		min-width: 0;
		min-height: 0;
		overflow-y: auto;
		background: var(--muted);
		/* SYMMETRIC INSETS, so the email is CENTRED IN THE FULL WIDTH. 68px each side: 20px of base inset plus the 44px blocks rail at its own 12px inset, which the email must never sit under. The RIGHT side MIRRORS it rather than taking the base 20px, and that is the whole of the centring — with 68/20 the email's centre lands 24px right of the workspace's, exactly the kind of "not quite centred" that reads as a mistake. The cost is 48px less room before the email overflows, paid at a width where it already scrolls. Nothing is reserved for the style panel; see `.workspace`. `--code-gutter` is added to the LEFT only, and is the one thing here that moves the email: a symmetric pair of insets means adding a gutter on one side shifts the centre by half of it, which IS "centred in the remainder". Zero when the view is shut, so the email does not move. */
		padding-block: 20px;
		padding-left: calc(68px + var(--code-gutter, 0px));
		padding-right: 68px;
		/* EASE THE DISPLACEMENT, NEVER THE MEMBERS' OWN DIMENSIONS. `width` must NEVER be transitioned on the head or the frame: the head is a Svelte snippet in this document and the frame is an iframe GrapesJS sizes itself, so easing either means the two are genuinely different widths for the whole duration — the "head and sheet are one column" rule broken on every open rather than at rest (`.gjs-email-head` records the same prohibition from the other end). This is on the COLUMN'S OWN PADDING, so head, mount and frame move as one piece and no member's dimensions change; it costs a layout pass per frame, cheap because nothing re-flows. A `transform: translateX` would be cheaper and is WRONG: this element is the SCROLLER and the grey backdrop, so translating it slides the backdrop off one edge and leaves a gap at the other. */
		transition: padding-left 180ms ease;
		box-sizing: border-box;
		scrollbar-width: thin;
	}
	/* No movement at all when the viewer has asked for none — straight to the end state, which is the same either way: the transition only changes how the padding gets there. */
	@media (prefers-reduced-motion: reduce) {
		.canvas-column {
			transition: none;
		}
	}
	.canvas-column::-webkit-scrollbar {
		width: 5px;
		height: 5px;
	}
	.canvas-column::-webkit-scrollbar-track {
		background: transparent;
	}
	.canvas-column::-webkit-scrollbar-thumb {
		background: var(--border);
		border-radius: 99px;
	}
	.canvas-column::-webkit-scrollbar-thumb:hover {
		background: var(--muted-foreground);
	}
	/* NEITHER CHILD MAY SHRINK, and this is what makes the column scroll at all. A flex item defaults to `flex-shrink: 1`, so with the column at a fixed height the head and the mount are both squashed to fit — the content never exceeds the box, so there is nothing to scroll and a long email is simply cut off at the fold. `flex: none` is the whole fix. Layout for the head lives here rather than in the injected GrapesJS sheet for the reason `.canvas-column` gives: that sheet is guarded by its element id and a hot reload never replaces it. */
	.gjs-email-head {
		flex: none;
		/* THE HEAD AND THE SHEET ARE ONE COLUMN: same width, same centre line, and the SAME behaviour when the space runs out. Every rule here exists to make the second half of that true, because the head is a Svelte snippet OUTSIDE GrapesJS's frame — nothing GrapesJS does can size it. THERE MUST BE NO `max-width: 100%`: it looks like the fix for the offset that `align-items: center` alone produces (a 640px head in a narrower box hangs off BOTH sides while GrapesJS's frame clamps left), but it buys that by making the head the one that GIVES — it shrinks to the box while the frame keeps its 640, so the subject band ends up measurably narrower than the sheet it belongs to (532 against 640 at a 946px workspace), two elements yielding by two different rules. */
		/* `margin-inline: auto` fixes it instead, and is exactly what GrapesJS's own frame wrapper uses: auto margins in a flex item's cross axis take over placement from `align-items`, and the spec resolves them to ZERO when the free space is negative — so the head centres while there is room and clamps to the content box's start edge when there is not, which is precisely what the frame does. Neither shrinks; both overflow right and the column scrolls (its `overflow-y: auto` computes `overflow-x` to `auto` too). `var(--device-w)` IS THE AUTHORITY, not a fallback: it is the same number `setDevice` hands GrapesJS, so it cannot lag, and with `.canvas`'s exact width the frame is that number too. `syncHeadWidth` (create.ts) still writes an inline width from the frame wrapper, and a stale one must never outrank this, which is what `setDevice` clears and why. `box-sizing` because the head has its own padding. NO TRANSITION on the width: GrapesJS resizes its frame instantly, so an eased head really is a different width from the sheet for the whole duration (measured at 19px each side mid-flight). */
		width: var(--device-w, 640px);
		margin-inline: auto;
		box-sizing: border-box;
		margin-bottom: 10px;
	}
	.loading {
		position: absolute;
		inset: 0;
		z-index: 30;
		display: flex;
		background: var(--card);
	}
	.loading-canvas {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		min-width: 0;
		/* THE SAME SUM as `.canvas-column`, not merely one that agrees today: a skeleton whose only job is that nothing moves at the reveal cannot be laid out by different arithmetic than the thing it stands in for. `--code-gutter` appears here too even though the code view cannot be opened before the editor is ready. */
		padding-block: 20px;
		padding-left: calc(68px + var(--code-gutter, 0px));
		padding-right: 68px;
		background: var(--muted);
		box-sizing: border-box;
	}
	/* The sheet's placeholder is the sheet's own box: same width as the device, same inset as `PAGE_INSET`, so the real page lands where this was rather than jumping at the reveal — which means it must also YIELD the same way, `margin-inline: auto` rather than a `max-width: 100%` cap, for the reason `.gjs-email-head` sets out. */
	.loading-sheet {
		display: flex;
		flex-direction: column;
		gap: 10px;
		width: var(--device-w, 640px);
		margin-inline: auto;
		padding: 32px 32px 36px;
		background: var(--card);
		box-sizing: border-box;
	}
	.loading-rail {
		position: absolute;
		top: 12px;
		right: 12px;
		bottom: 12px;
		/* THE PANEL'S OWN WIDTH — must stay in step with `.rail-right`, or the reveal jumps sideways by the difference. */
		width: 320px;
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 12px 8px;
		border-radius: 12px;
		background: var(--email-chrome);
		box-shadow: var(--elev-2);
		outline: 1px solid color-mix(in oklab, var(--foreground) 10%, transparent);
		outline-offset: -1px;
		box-sizing: border-box;
	}
	.canvas {
		flex: none;
		/* AN EXACT WIDTH, NOT A FLOOR — the mount IS the email's width, which is what makes the frame inside it correct at every instant rather than eventually. GrapesJS applies a device's width to `.gjs-frame-wrapper` inline only when the device CHANGES; on a fresh editor it applies none, and a wrapper with no width FILLS its container (reproduced by clearing that inline style: wrapper and iframe both went to 800px, the mount's width). So a cold load has a window in which the frame is the container's width and not the device's, and `syncHeadWidth` — which measures the wrapper and writes the result onto the head — can land inside it; its ResizeObserver never corrects the reading (measured: zero firings for a real resize of that node), so the head kept 800px against a 640px device for the whole session — the two AGREED, at the wrong width, which is why touching the device toggle fixed it permanently and why it read as a device-toggle bug. A FLOOR CANNOT FIX THAT: `min-width` alone lets the mount be WIDER than the device, so "fills its container" is still 800. */
		/* IT MUST BE A MIN/MAX PAIR AND NOT `width`: GrapesJS writes an inline `width: auto` onto this element at init, an inline declaration beats any stylesheet rule, so `width: var(--device-w)` here is inert — and inert in a way that looks fine, since `width: auto` on a flex item with auto margins resolves to its content (measured: 0px wide, `margin-inline` eating 400px a side). Neither bound is written inline, so pinning both to one value wins; safe because the wrapper's `max-width` computes to `none`, not `100%` — checked, not assumed. `margin-inline: auto` and NOT the column's `align-items: center`: auto margins take over cross-axis placement and the spec resolves them to ZERO on negative free space, so this centres while there is room and clamps to the content box's start edge when there is not, where `align-items: center` gives a negative offset and hangs the mount off BOTH sides while the head clamps left. The HEIGHT is written by `syncFrameHeight`; `.gjs-editor` and `.gjs-cv-canvas` are both `height: 100%`, so this is the one measurement they derive from and what the column scrolls, and the floor matches the frame's own so a sync that has not run leaves a full-size canvas rather than a collapsed one. */
		min-width: var(--device-w, 640px);
		max-width: var(--device-w, 640px);
		margin-inline: auto;
		min-height: 600px;
	}
</style>
