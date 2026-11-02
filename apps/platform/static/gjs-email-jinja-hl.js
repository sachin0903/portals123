/**
 * Jinja control-flow highlighter for the EMAIL editor's GrapesJS canvas iframe, where copy and logic
 * look identical. It MARKS every `{% … %}` and `{{ … }}` and never redraws one: the monospace overprint
 * this was is wider than the copy's face, so chips fitted to 9-11px and the longest to a 1.19:1 wash.
 *
 * THE OVERLAY RULE (do not weaken): `editor.getHtml()` is saved and emailed, so nothing here may touch
 * a content node — no wrapping span, no split text node, no rewritten textContent, no
 * `Range.surroundContents`. Decorations are absolute <div>s measured off `Range.getClientRects()` on
 * ONE layer appended to `document.body`, outside the MODEL `getHtml()` serialises, so nothing ships and
 * GrapesJS has nothing to commit or undo. It is `grapesjs-jinja-vars.js`'s technique and its reason:
 * wrapping tokens fragmented flex text runs AND leaked into the export.
 *
 * NEVER DRAW BELOW THE CONTENT: the frame is sized to `body.scrollHeight` under a ResizeObserver on it
 * (`create.ts::syncFrameHeight`), so anything past the last line grows the measurement and the frame
 * grows to match — no trailing gutter, no badge off the last line, no chip padded downwards. OFF is
 * `{ type: 'gjs-jinja-hl', on: false }` from the parent (the document editor's message name, kept
 * identical) and takes the whole layer, not its paint. Colours are LITERALS: app variables stay out.
 */
(function () {
	'use strict';

	var OVERLAY_ID = 'gjs-email-jinja-hl';
	// The `{{` picker's own menu, in SKIP_SELECTOR because its rows PRINT tokens.
	var MENU_ID = 'gjs-email-field-menu';

	// GrapesJS re-injects `canvas.scripts` when it re-renders a frame's body without reloading the
	// document, so the first evaluation owns the wiring: a second set of listeners would draw twice.
	if (window.__gjsEmailJinjaHl) {
		window.__gjsEmailJinjaHl.redraw();
		return;
	}

	// The matcher, mirrored at pkgs/ui/src/lib/components/email-editor/jinja-highlight.ts, whose test
	// runs one case table through both. Change one, change the other — and keep the markers below.

	// ── jinjaRuns: BEGIN (mirrored in pkgs/ui/src/lib/components/email-editor/jinja-highlight.ts) ──
	/** Every Jinja run, left to right, as `{ start, end, kind }`, `end` exclusive; 'tag' is `{% … %}`
	 *  control flow, 'field' a `{{ … }}` value. A run needs its closer, or a half-typed `{% if` would
	 *  swallow the paragraph, and the scan resumes past the opener so a later well-formed run still
	 *  decorates. It ends at the FIRST closer and resumes past that, never inside, so `{% if x == "{{" %}`
	 *  and a `{{ … }}` in a tag each decorate once. `{# … #}` never sends, so it is unmatched. */
	function jinjaRuns(text) {
		var runs = [];
		if (!text) return runs;
		var i = 0;
		while (i < text.length) {
			var field = text.indexOf('{{', i);
			var tag = text.indexOf('{%', i);
			if (field === -1 && tag === -1) break;
			// The earliest opener wins; -1 means "not present", never "at the front".
			var isTag = field === -1 || (tag !== -1 && tag < field);
			var open = isTag ? tag : field;
			var close = text.indexOf(isTag ? '%}' : '}}', open + 2);
			if (close === -1) {
				i = open + 2;
				continue;
			}
			runs.push({ start: open, end: close + 2, kind: isTag ? 'tag' : 'field' });
			i = close + 2;
		}
		return runs;
	}
	// ── jinjaRuns: END ──

	// ── Paint ───────────────────────────────────────────────────────────────────────────────────────

	// Never decorated: our own layer (a stale one would be scanned and chipped); the element GrapesJS's
	// RTE holds, the only one carrying `contenteditable="true"`, a chip under a moving caret lagging a
	// keystroke behind; and its tooling plus <script>/<style>, which legitimately carries tokens.
	var SKIP_SELECTOR =
		'#' + OVERLAY_ID + ',#' + MENU_ID + ',[contenteditable="true"],#gjs-tools,script,style';

	// A HIGHLIGHTER, not an overprint: the old washed chip measured 1.19:1 against its own background
	// where un-chipped copy is 2.60:1. `mix-blend-mode: multiply` makes tint-and-ring work, darkening the
	// backdrop but not dark text (2.53:1 after). Neutral for both kinds; two greys give body and edge.
	var CHIP_FILL = '#e9ebf0';
	var CHIP_RING = '#ccd2e0';

	var enabled = true; // editor default; the parent's message is authoritative
	var timer = null;
	var observer = null;

	function clearOverlay() {
		// By id, not a held reference: a replaced body can leave a layer nothing holds a handle to.
		var existing = document.getElementById(OVERLAY_ID);
		if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
	}

	function isSkipped(node) {
		var el = node.parentNode;
		if (!el || el.nodeType !== Node.ELEMENT_NODE || !el.closest) return true;
		return !!el.closest(SKIP_SELECTOR);
	}

	function chip(rect, origin) {
		var d = document.createElement('div');
		d.style.cssText =
			'position:absolute;pointer-events:none;box-sizing:border-box;border-radius:3px;' +
			'left:' +
			(rect.left - origin.left - 1) +
			'px;top:' +
			(rect.top - origin.top - 1) +
			'px;width:' +
			(rect.width + 2) +
			'px;height:' +
			(rect.height + 2) +
			'px;background:' +
			CHIP_FILL +
			';box-shadow:inset 0 0 0 1px ' +
			CHIP_RING +
			';' +
			// Multiply can only darken, which is why the fill can be a real grey and not a near-white.
			'mix-blend-mode:multiply;';
		return d;
	}

	function draw() {
		// A second evaluation can ask for a redraw while the document is still loading, before any body.
		if (!document.body) return;
		// Detach around our own writes, or the layer landing in the observed subtree redraws for ever.
		if (observer) observer.disconnect();
		clearOverlay();
		if (enabled) {
			// Against the body's border box, so the layer travels with the card when the column scrolls.
			var origin = document.body.getBoundingClientRect();
			var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
			var frag = document.createDocumentFragment();
			var any = false;
			var node;
			while ((node = walker.nextNode())) {
				var text = node.textContent;
				if (!text || (text.indexOf('{{') === -1 && text.indexOf('{%') === -1)) continue;
				if (isSkipped(node)) continue;
				var runs = jinjaRuns(text);
				for (var r = 0; r < runs.length; r++) {
					var run = runs[r];
					var range = document.createRange();
					range.setStart(node, run.start);
					range.setEnd(node, run.end);
					var rects = range.getClientRects();
					for (var i = 0; i < rects.length; i++) {
						var rect = rects[i];
						// A run in a hidden element measures as nothing, and a chip would land top-left of the card.
						if (rect.width < 2 || rect.height < 2) continue;
						// EVERY rect, so a tag wrapped across two lines is marked on both — an overprint could not.
						frag.appendChild(chip(rect, origin));
						any = true;
					}
				}
			}
			if (any) {
				var layer = document.createElement('div');
				layer.id = OVERLAY_ID;
				// NO NUMERIC Z-INDEX: a numeric one makes a STACKING CONTEXT, and `mix-blend-mode` blends only
				// within its own, so the chips would composite against this layer's transparent backdrop and
				// paint as flat opaque grey. `z-index: auto` makes none, and the last positioned child wins.
				layer.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;';
				layer.appendChild(frag);
				document.body.appendChild(layer);
			}
		}
		if (observer) {
			// documentElement, not body: GrapesJS can replace the body wholesale (an observer on the old
			// node then goes quiet without erroring), and the Style rail's rules land in a HEAD <style>.
			// `attributeFilter: ['style']` because this editor styles INLINE; class redraws at pointer speed.
			observer.observe(document.documentElement, {
				childList: true,
				subtree: true,
				characterData: true,
				attributes: true,
				attributeFilter: ['style']
			});
		}
	}

	function schedule() {
		if (timer) return;
		timer = setTimeout(function () {
			timer = null;
			draw();
		}, 60);
	}

	function setEnabled(on) {
		if (on === enabled) return;
		enabled = on;
		draw(); // immediate, not debounced: preview must not open over a frame of stale chips
	}

	// PINNED TO THE EDITOR SHELL, SOURCE FIRST. This frame has no `src`, so its parent window is the only
	// one that can legitimately post to it — any other holder of a WindowProxy (an embed, a `window.open`)
	// would otherwise reach the insert below with markup of its own, in the portal's own origin and under
	// the author's session. THE PARENT OWNS THE MARKUP (see the picker's note), so this pin is the WHOLE of
	// the defence: nothing downstream sanitises what arrives, and the source pin is the half that holds
	// even where a frame reports an origin of its own.
	window.addEventListener('message', function (e) {
		if (e.source !== window.parent) return;
		if (e.origin !== window.location.origin) return;
		if (!e.data) return;
		if (e.data.type === 'gjs-jinja-hl') return setEnabled(!!e.data.on);
		// The picker's vocabulary, re-sent on change. It stays inert until the first: no menu beats empty.
		if (e.data.type === 'gjs-email-fields') {
			fieldGroups = Array.isArray(e.data.groups) ? e.data.groups : [];
			if (menuVisible()) closeMenu();
		}
		// The RAIL asking for a field AT THE CARET: the parent decides when (it knows whether an RTE is
		// live), this side is the only one that CAN insert, and it is the picker's own insert. (Named
		// without call parentheses: this half is scanned for DOM-mutating calls, and a quote would fail.)
		if (e.data.type === 'gjs-email-insert-field') insertAtCaret(e.data.html, e.data.token);
	});

	// THE `{{` PICKER — merge fields at the caret, filtered as you type; the one an author finds
	// unprompted, where the rail's flyout had to be named in the starter copy to be found. THE PARENT
	// OWNS THE MARKUP: each field arrives with the HTML the rail's own drag carries
	// (`email-editor.svelte::fieldDragPayload` → `create.ts::fieldSpanHtml`), so insert, drag and flyout
	// give one component; a second copy in an unimportable static asset would drift until a template lost
	// its format. `insertHTML`, not a component call: the RTE's re-parse lets `isComponent` claim the span.
	var fieldGroups = []; // [{ group, fields: [{ label, token, html }] }] — supplied by the parent
	var menuEl = null;
	var rows = []; // flattened, in display order: [{ label, token, html }]
	var activeRow = 0;
	var trigger = null; // { node, start, end, query } — the `{{…` text a pick replaces

	function menuVisible() {
		return !!menuEl;
	}

	function closeMenu() {
		if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
		menuEl = null;
		rows = [];
		activeRow = 0;
		trigger = null;
	}

	/** Case-insensitive substring over label, token and group — the same rule as the rail's own
	 *  `merge-fields.ts::matchFields`, mirrored because a classic script cannot import. */
	function matching(query) {
		var q = String(query || '')
			.trim()
			.toLowerCase();
		var out = [];
		for (var i = 0; i < fieldGroups.length; i++) {
			var g = fieldGroups[i];
			var hay = String(g.group || '').toLowerCase();
			var hits = [];
			var fields = g.fields || [];
			for (var j = 0; j < fields.length; j++) {
				var f = fields[j];
				var label = String(f.label || '').toLowerCase();
				var token = String(f.token || '').toLowerCase();
				if (
					q === '' ||
					label.indexOf(q) !== -1 ||
					token.indexOf(q) !== -1 ||
					hay.indexOf(q) !== -1
				) {
					hits.push(f);
				}
			}
			if (hits.length) out.push({ group: g.group, fields: hits });
		}
		return out;
	}

	/** The `{{…` the author has typed, or null. Needs a COLLAPSED selection in a text node inside the
	 *  element the RTE is editing, and reads only the text before the caret there. A brace closes the
	 *  question: `}` means the token is finished, a second `{` that the nearest opener is the live one. */
	function readTrigger() {
		var sel = document.getSelection();
		if (!sel || !sel.isCollapsed || !sel.rangeCount) return null;
		var range = sel.getRangeAt(0);
		var node = range.startContainer;
		if (!node || node.nodeType !== Node.TEXT_NODE) return null;
		var host = node.parentNode;
		if (!host || !host.closest || !host.closest('[contenteditable="true"]')) return null;
		var before = String(node.textContent || '').slice(0, range.startOffset);
		var open = before.lastIndexOf('{{');
		if (open === -1) return null;
		var inner = before.slice(open + 2);
		if (inner.indexOf('}') !== -1 || inner.indexOf('{') !== -1) return null;
		return { node: node, start: open, end: range.startOffset, query: inner };
	}

	function styleMenu(el) {
		el.id = MENU_ID;
		el.setAttribute('role', 'listbox');
		// The rows stop their own presses (`pickOn`); this covers the panel, so none ends the RTE session.
		el.addEventListener('mousedown', function (ev) {
			if (ev.target === el || !el.querySelector('[role="option"]')) ev.preventDefault();
			ev.stopPropagation();
		});
		// Literals, like every colour here: the app's CSS variables do not cross into the canvas document.
		el.style.cssText =
			'position:absolute;z-index:2147483646;min-width:180px;max-width:260px;max-height:238px;' +
			'overflow-y:auto;background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;' +
			'box-shadow:0 8px 24px rgba(15,23,42,.14);padding:4px;font-family:' +
			'ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:12px;color:#0f172a;';
	}

	// Outside the render loop so each row captures its own index: a `var` would give every row the last.
	function pickOn(index) {
		return function (ev) {
			// preventDefault KEEPS THE FOCUS (else the press moves it off the contenteditable before the
			// insert reads the caret); stopPropagation KEEPS THE RTE (else GrapesJS's document-level
			// mousedown ends rich-text editing, so the field lands and the next keystroke goes nowhere).
			ev.preventDefault();
			ev.stopPropagation();
			if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
			pick(index);
		};
	}

	function hoverOn(index) {
		return function () {
			activeRow = index;
			paintActive();
		};
	}

	function paintActive() {
		var options = menuEl ? menuEl.querySelectorAll('[role="option"]') : [];
		for (var i = 0; i < options.length; i++) {
			var on = i === activeRow;
			options[i].style.background = on ? '#eef2ff' : 'transparent';
			options[i].style.color = on ? '#4338ca' : '#0f172a';
			options[i].setAttribute('aria-selected', on ? 'true' : 'false');
			// AND SCROLL IT INTO VIEW. The panel is its own scroller (~10 rows of a ~17-field
			// vocabulary), so without this the highlight walked off the bottom: the list did not move,
			// no row looked selected, and Enter inserted a field the author never saw. `block: 'nearest'`
			// so a row already visible does not jog the list under the pointer.
			if (on && options[i].scrollIntoView) {
				options[i].scrollIntoView({ block: 'nearest' });
			}
		}
	}

	function renderMenu(groups) {
		rows = [];
		menuEl.textContent = '';
		if (!groups.length) {
			var none = document.createElement('div');
			none.style.cssText = 'padding:6px 8px;color:#64748b;';
			none.textContent = 'No matching field';
			menuEl.appendChild(none);
			return;
		}
		for (var i = 0; i < groups.length; i++) {
			var head = document.createElement('div');
			head.style.cssText =
				'padding:6px 8px 2px;font-size:9px;font-weight:700;letter-spacing:.08em;' +
				'text-transform:uppercase;color:#64748b;';
			head.textContent = groups[i].group;
			menuEl.appendChild(head);
			var fields = groups[i].fields;
			for (var j = 0; j < fields.length; j++) {
				var index = rows.length;
				rows.push(fields[j]);
				var row = document.createElement('div');
				row.setAttribute('role', 'option');
				row.style.cssText =
					'padding:4px 8px;border-radius:4px;cursor:pointer;white-space:nowrap;' +
					'overflow:hidden;text-overflow:ellipsis;';
				row.textContent = fields[j].label;
				// mousedown, not click: a click arrives after the press has taken the caret the insert needs.
				row.addEventListener('mousedown', pickOn(index));
				row.addEventListener('mouseenter', hoverOn(index));
				menuEl.appendChild(row);
			}
		}
		paintActive();
	}

	/** Put the menu just under the `{{`, and never off the bottom or the right of the frame. */
	function placeMenu() {
		if (!menuEl || !trigger) return;
		var probe = document.createRange();
		try {
			probe.setStart(trigger.node, trigger.start);
			probe.setEnd(trigger.node, trigger.end);
		} catch {
			return;
		}
		var rect = probe.getBoundingClientRect();
		var box = menuEl.getBoundingClientRect();
		var left = rect.left + window.scrollX;
		var top = rect.bottom + window.scrollY + 4;
		var maxLeft = window.scrollX + document.documentElement.clientWidth - box.width - 8;
		if (left > maxLeft) left = Math.max(window.scrollX + 8, maxLeft);
		// Above the caret with no room below: the frame is only as tall as the email.
		if (rect.bottom + box.height + 12 > document.documentElement.clientHeight) {
			var above = rect.top + window.scrollY - box.height - 4;
			if (above > window.scrollY) top = above;
		}
		menuEl.style.left = left + 'px';
		menuEl.style.top = top + 'px';
	}

	function openMenu() {
		if (!fieldGroups.length) return;
		trigger = readTrigger();
		if (!trigger) return closeMenu();
		if (!menuEl) {
			menuEl = document.createElement('div');
			styleMenu(menuEl);
			document.body.appendChild(menuEl);
		}
		activeRow = 0;
		renderMenu(matching(trigger.query));
		placeMenu();
	}

	/** Put `html` where the caret is, and say so. Refuses unless the caret is inside an element GrapesJS
	 *  is editing: with no selection an insert lands at the start of the document, with a stale one
	 *  wherever the caret last was. Success goes back as `gjs-email-field-inserted`, marking it dirty. */
	// THE LAST EDITED ELEMENT, remembered continuously. A rail click cannot preserve the caret: moving
	// focus out of the frame makes GrapesJS end the RTE session, and ending it RE-PARSES the element —
	// which detaches the very nodes a saved Range points at. So the exact offset is used when it
	// survives, and otherwise the insert goes to the END of the element the author was last typing in.
	// That is "where you were typing" in the only sense that is still true, and it is the whole
	// difference between this and a new block appended at the bottom of the email.
	// The COMPONENT ID, not the element: GrapesJS sets an element's `id` to its component's id (that is
	// how the StyleManager targets it), and the re-parse mints a new ELEMENT carrying the same id — so a
	// held element reference goes stale where the id does not.
	var lastEditableId = null;

	document.addEventListener('selectionchange', function () {
		var sel = document.getSelection();
		if (!sel || !sel.rangeCount) return;
		var node = sel.anchorNode;
		var host = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentNode);
		var editable = host && host.closest ? host.closest('[contenteditable="true"]') : null;
		if (editable && editable.id) lastEditableId = editable.id;
	});

	/** The live caret's editable element, or null. A detached range is exactly what the re-parse
	 *  leaves behind, so `isConnected` is the test that matters. */
	function liveEditable() {
		var sel = document.getSelection();
		if (!sel || !sel.rangeCount) return null;
		var node = sel.anchorNode;
		if (!node || !node.isConnected) return null;
		var host = node.nodeType === Node.ELEMENT_NODE ? node : node.parentNode;
		return host && host.closest ? host.closest('[contenteditable="true"]') : null;
	}

	function insertAtCaret(html, token) {
		if (!html) return refuse(token);
		var editable = liveEditable();
		// NO LIVE CARET is the normal case for a RAIL click, not an edge one: moving focus out of this
		// frame makes GrapesJS end the RTE session, and ending it re-parses the element and detaches the
		// range. So the parent is told WHICH COMPONENT was being typed in and appends into it through
		// the model — which is undoable, and lands the field in that paragraph rather than in a new
		// block at the bottom of the email.
		if (!editable) return refuse(token);
		// FOCUS FIRST: `execCommand` acts on the focused document.
		editable.focus();
		// NO TRAILING SPACE. `pick()` removed its own for the same reason ("cost a visible gap"), and
		// this path writes into content `editor.getHtml()` SERIALISES — so an `&nbsp;` here is a gap in
		// the sent email, and the two insert paths for one act produced different saved HTML.
		if (!document.execCommand('insertHTML', false, html)) return refuse(token);
		try {
			window.parent.postMessage(
				{ type: 'gjs-email-field-inserted', token: token, ok: true },
				window.location.origin
			);
		} catch {
			/* the parent only uses this to mark the draft dirty */
		}
		schedule(); // the new token wants a chip
	}

	/** No live caret. SAID OUT LOUD — the parent cannot see into this document — and WITH the component
	 *  the author was last typing in, so the parent can put the field there. */
	function refuse(token) {
		try {
			window.parent.postMessage(
				{
					type: 'gjs-email-field-inserted',
					token: token,
					ok: false,
					componentId: lastEditableId
				},
				window.location.origin
			);
		} catch {
			/* nothing to do */
		}
	}
	/** Replace the typed `{{…` with the field's own markup. */
	function pick(index) {
		var field = rows[index];
		if (!field || !trigger) return closeMenu();
		var range = document.createRange();
		try {
			range.setStart(trigger.node, trigger.start);
			range.setEnd(trigger.node, trigger.end);
		} catch {
			return closeMenu();
		}
		var sel = document.getSelection();
		sel.removeAllRanges();
		sel.addRange(range);
		var html = field.html;
		// Closed BEFORE the insert, so `trigger`'s nodes are dropped before the RTE re-reads the element.
		closeMenu();
		// execCommand is what GrapesJS's own RTE uses, and the only insert leaving the undo stack and the
		// caret in a state it recognises. NO TRAILING SPACE: the `&nbsp;` that was here guarded against
		// "landing inside the token", which its `contenteditable="false"` prevents, and cost a visible gap.
		document.execCommand('insertHTML', false, html);
		try {
			window.parent.postMessage(
				{ type: 'gjs-email-field-inserted', token: field.token },
				window.location.origin
			);
		} catch {
			/* the parent only uses this to mark the draft dirty */
		}
		schedule(); // the new token wants a chip
	}

	function onPickerKey(e) {
		if (!menuVisible()) return;
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			if (!rows.length) return;
			activeRow = (activeRow + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
			paintActive();
			return;
		}
		if (e.key === 'Enter' || e.key === 'Tab') {
			// Only when there is something to choose. With no match, Enter is the author's own newline.
			if (!rows.length) return;
			e.preventDefault();
			pick(activeRow);
			return;
		}
		if (e.key === 'Escape') {
			e.preventDefault();
			closeMenu();
		}
	}

	function initPicker() {
		// ASK, rather than wait to be told: GrapesJS evaluates `canvas.scripts` after the editor's own
		// init, so a push at init time is a race the message loses silently — the picker never opens. A
		// request from here cannot be early, and re-arms whenever GrapesJS re-evaluates this file.
		try {
			window.parent.postMessage({ type: 'gjs-email-fields-request' }, window.location.origin);
		} catch {
			/* no parent to ask — the picker stays inert, which is the honest failure */
		}
		// Capture phase, so it is seen wherever in the frame the RTE currently is.
		document.addEventListener(
			'input',
			function () {
				if (readTrigger()) openMenu();
				else if (menuVisible()) closeMenu();
			},
			true
		);
		document.addEventListener('keydown', onPickerKey, true);
		// Deliberately NOT `selectionchange`: the insert moves the selection, and closing on it races pick.
		document.addEventListener('mousedown', function (e) {
			if (menuEl && !menuEl.contains(e.target)) closeMenu();
		});
		document.addEventListener('focusout', closeMenu, true);
		window.addEventListener('resize', closeMenu, { passive: true });
	}

	function init() {
		observer = new MutationObserver(schedule);
		initPicker();
		draw();
		// The frame IS the device, so switching one resizes this window and re-wraps every measured line.
		window.addEventListener('resize', schedule, { passive: true });
		document.addEventListener('input', schedule, true);
		// focusin/focusout are when a node starts and stops being one to leave alone.
		document.addEventListener('focusin', schedule, true);
		document.addEventListener('focusout', schedule, true);
		// A webfont arriving after first paint re-wraps the copy under chips already positioned.
		if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
			document.fonts.ready.then(schedule);
		}
	}

	window.__gjsEmailJinjaHl = { redraw: schedule, setEnabled: setEnabled };

	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
	else init();
})();
