/**
 * Jinja variable helper — runs inside the GrapesJS canvas iframe.
 *
 * Shows an autocomplete dropdown when the user types {{ in a contenteditable
 * and inserts the chosen variable as a plain {{ expr }} text token.
 *
 * Tokens stay as ordinary text in the DOM (never wrapped in spans) — wrapping
 * fragmented flex text runs and leaked into export. To still make them stand out
 * in the EDITOR, a highlight overlay paints a subtle tint behind each {{ token }}
 * using Range rects, without touching the content. It's editor-only: the parent
 * sends { type: 'gjs-jinja-hl', on } to hide it in preview, and the overlay is a
 * plain body layer (not a component) so it never appears in the saved HTML.
 * The parent also sends { type: 'gjs-vars-update', vars: [{key,label}] } to feed
 * the autocomplete list.
 */
(function () {
	'use strict';

	// ── Variable + filter lists (populated from parent via postMessage) ────────
	var vars = [];
	var filters = []; // [{ label, snippet }] — offered after a | inside a {{ token }}

	// Last caret position seen inside a contenteditable, kept live so the parent's "Insert field"
	// picker can drop a token at the caret even though opening the picker moves focus OUT of this
	// iframe (which would otherwise collapse/clear the selection). We snapshot on every selectionchange
	// while the caret is in editable text; the insert handler restores it. If GrapesJS has since torn
	// down the text node (RTE left edit mode and re-rendered), the range is stale — the handler detects
	// that and tells the parent to fall back to a component-level append.
	var savedRange = null;

	function editableHost(node) {
		var el = node && node.nodeType === Node.ELEMENT_NODE ? node : node && node.parentNode;
		return el && el.closest ? el.closest('[contenteditable="true"]') : null;
	}

	function captureCaret() {
		var sel = document.getSelection();
		if (!sel || sel.rangeCount === 0) return;
		var r = sel.getRangeAt(0);
		if (editableHost(r.startContainer)) savedRange = r.cloneRange();
	}
	document.addEventListener('selectionchange', captureCaret);
	// focusout fires just before the iframe loses focus to the parent picker — grab the caret one last
	// time while the selection is still authoritative.
	document.addEventListener('focusout', captureCaret, true);

	window.addEventListener('message', function (e) {
		if (!e.data) return;
		if (e.data.type === 'gjs-vars-update' && Array.isArray(e.data.vars)) {
			vars = e.data.vars;
			if (Array.isArray(e.data.filters)) filters = e.data.filters;
		} else if (e.data.type === 'gjs-jinja-insert' && typeof e.data.value === 'string') {
			insertTokenAtCaret(e.data.value);
		}
	});

	/** Insert `{{ value }}` at the (possibly restored) caret. Posts { type: 'gjs-jinja-inserted', ok }
      back to the parent so it can append at the component level when there's no usable caret. */
	function insertTokenAtCaret(value) {
		var token = '{{ ' + value + ' }}';
		var sel = document.getSelection();
		// Prefer a live in-editable selection; otherwise fall back to the snapshot taken before focus left.
		var range = null;
		if (sel && sel.rangeCount && editableHost(sel.getRangeAt(0).startContainer)) {
			range = sel.getRangeAt(0);
		} else if (
			savedRange &&
			savedRange.startContainer &&
			savedRange.startContainer.isConnected &&
			editableHost(savedRange.startContainer)
		) {
			range = savedRange;
		}
		if (!range) {
			try {
				window.parent.postMessage({ type: 'gjs-jinja-inserted', ok: false, value: value }, '*');
			} catch (err) {}
			return;
		}
		var host = editableHost(range.startContainer);
		range.deleteContents(); // replace any selected text
		var textNode = document.createTextNode(token);
		range.insertNode(textNode);
		// Caret just after the inserted token.
		var after = document.createRange();
		after.setStart(textNode, textNode.length);
		after.collapse(true);
		sel.removeAllRanges();
		sel.addRange(after);
		savedRange = after.cloneRange();
		if (host) {
			host.normalize();
			// Let GrapesJS's RTE commit the change to the component model, and the highlight overlay redraw.
			host.dispatchEvent(new Event('input', { bubbles: true }));
		}
		try {
			window.parent.postMessage({ type: 'gjs-jinja-inserted', ok: true, value: value }, '*');
		} catch (err) {}
	}

	// ── Autocomplete dropdown ─────────────────────────────────────────────────

	var dropdown = null;
	var activeIndex = -1;
	var pendingHide = null;

	function hideDropdown() {
		if (pendingHide) {
			clearTimeout(pendingHide);
			pendingHide = null;
		}
		if (dropdown && dropdown.parentNode) dropdown.parentNode.removeChild(dropdown);
		dropdown = null;
		activeIndex = -1;
	}

	function escHtml(s) {
		return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
	}

	function getCaretContext() {
		var sel = document.getSelection();
		if (!sel || sel.rangeCount === 0) return null;
		var range = sel.getRangeAt(0);
		if (!range.collapsed) return null;
		var node = range.startContainer;
		if (node.nodeType !== Node.TEXT_NODE) return null;
		var textBefore = node.textContent.slice(0, range.startOffset);
		var m = textBefore.match(/\{\{[^}]*$/);
		if (!m) return null;
		var matchStart = textBefore.length - m[0].length; // index of the '{{'
		var inner = m[0].slice(2); // everything after '{{' up to the caret
		var pipeIdx = inner.lastIndexOf('|');
		if (pipeIdx !== -1) {
			// Caret is after a pipe → completing a FILTER (the text after the last |).
			var afterPipe = inner.slice(pipeIdx + 1);
			var lead = afterPipe.length - afterPipe.replace(/^\s*/, '').length;
			return {
				node: node,
				mode: 'filter',
				needsPipe: false,
				partial: afterPipe.replace(/^\s*/, ''),
				matchStart: matchStart,
				filterStart: matchStart + 2 + pipeIdx + 1 + lead // node-text offset of the filter partial
			};
		}
		// No pipe yet, but a complete variable followed by a space → offer FILTERS (insert with a
		// leading pipe). This makes filters discoverable: type `{{ client.name ` and they appear.
		var mDone = inner.match(/^(\s*[\w.]+\s+)(.*)$/);
		if (mDone) {
			return {
				node: node,
				mode: 'filter',
				needsPipe: true,
				partial: mDone[2],
				matchStart: matchStart,
				filterStart: matchStart + 2 + mDone[1].length
			};
		}
		return {
			node: node,
			mode: 'var',
			partial: inner.replace(/^\s*/, ''),
			matchStart: matchStart
		};
	}

	function insertCompletion(value, mode) {
		var ctx = getCaretContext();
		if (!ctx) {
			hideDropdown();
			return;
		}
		var sel = document.getSelection();
		var caretOffset = sel.getRangeAt(0).startOffset;
		var text = ctx.node.textContent;
		var insertText, caretPos;
		if (mode === 'filter') {
			// Replace the partial (after the last pipe, or after the variable+space) with the chosen
			// filter. needsPipe means there's no pipe yet, so prepend one. Trailing space lets another
			// " | filter" or the closing "}}" follow naturally.
			var fBefore = text.slice(0, ctx.filterStart);
			var fAfter = text.slice(caretOffset);
			var piece = (ctx.needsPipe ? '| ' : '') + value + ' ';
			insertText = fBefore + piece + fAfter;
			caretPos = fBefore.length + piece.length;
		} else {
			// Variable → a complete {{ key }} token.
			var before = text.slice(0, ctx.matchStart);
			var after = text.slice(caretOffset);
			var token = '{{ ' + value + ' }}';
			insertText = before + token + after;
			caretPos = before.length + token.length;
		}
		ctx.node.textContent = insertText;
		var newRange = document.createRange();
		newRange.setStart(ctx.node, caretPos);
		newRange.collapse(true);
		sel.removeAllRanges();
		sel.addRange(newRange);
		hideDropdown();
	}

	function setActive(i) {
		if (!dropdown) return;
		var items = dropdown.querySelectorAll('[data-ac-item]');
		for (var j = 0; j < items.length; j++) {
			items[j].style.background = j === i ? '#f1f5f9' : '';
		}
		activeIndex = i;
	}

	function showDropdown(ctx, caretRect) {
		hideDropdown();
		var partial = ctx.partial.toLowerCase();
		// Filter mode (after a |) offers jinja filters; otherwise the variable list.
		var isFilter = ctx.mode === 'filter';
		var source = isFilter
			? filters.map(function (f) {
					return { key: f.snippet, label: f.label };
				})
			: vars;
		if (!source.length) return;
		var filtered = source.filter(function (v) {
			return (
				!partial ||
				v.key.toLowerCase().indexOf(partial) !== -1 ||
				(v.label && v.label.toLowerCase().indexOf(partial) !== -1)
			);
		});
		if (!filtered.length) return;

		dropdown = document.createElement('div');
		dropdown.setAttribute('role', 'listbox');
		dropdown.style.cssText = [
			'position:fixed',
			'z-index:99999',
			'background:#ffffff',
			'border:1px solid #e2e8f0',
			'border-radius:6px',
			'box-shadow:0 4px 20px rgba(0,0,0,.12),0 1px 4px rgba(0,0,0,.08)',
			'min-width:190px',
			'max-width:300px',
			'max-height:210px',
			'overflow-y:auto',
			'padding:4px',
			'font-family:inherit',
			'font-size:12px',
			'line-height:1'
		].join(';');

		var top = caretRect.bottom + 6;
		if (top + 220 > window.innerHeight) top = Math.max(8, caretRect.top - 220);
		var left = Math.max(8, Math.min(caretRect.left, window.innerWidth - 310));
		dropdown.style.left = left + 'px';
		dropdown.style.top = top + 'px';

		var lastGroup = null;
		filtered.forEach(function (v, i) {
			// Section header when the group changes (variable mode only; filters have no groups). Headers
			// aren't [data-ac-item], so keyboard nav + activeIndex skip them.
			if (!isFilter && v.group && v.group !== lastGroup) {
				lastGroup = v.group;
				var hdr = document.createElement('div');
				hdr.textContent = v.group;
				hdr.style.cssText =
					'padding:6px 8px 2px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;';
				dropdown.appendChild(hdr);
			}
			var item = document.createElement('div');
			item.setAttribute('role', 'option');
			item.setAttribute('data-ac-item', '1');
			item.style.cssText =
				'display:flex;align-items:baseline;gap:8px;padding:6px 8px;border-radius:4px;cursor:pointer;';
			item.setAttribute('data-ac-value', v.key);
			item.setAttribute('data-ac-mode', isFilter ? 'filter' : 'var');
			item.innerHTML =
				'<span style="font-family:monospace;font-size:11px;color:#4338ca;flex-shrink:0;white-space:nowrap">' +
				escHtml(isFilter ? v.label || v.key : v.key) +
				'</span>' +
				(isFilter
					? '<span style="font-size:11px;color:#94a3b8;min-width:0;white-space:nowrap">| ' +
						escHtml(v.key) +
						'</span>'
					: v.label
						? '<span style="font-size:11px;color:#94a3b8;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
							escHtml(v.label) +
							'</span>'
						: '');
			(function (val, mode) {
				item.addEventListener('mousedown', function (ev) {
					ev.preventDefault();
					insertCompletion(val, mode);
				});
				item.addEventListener('mouseenter', function () {
					setActive(i);
				});
			})(v.key, isFilter ? 'filter' : 'var');
			dropdown.appendChild(item);
		});

		document.body.appendChild(dropdown);
		setActive(0);
	}

	// ── Event wiring ──────────────────────────────────────────────────────────

	/** Insert a zero-width marker at the caret, measure it, then remove it.
      This is the only reliable way to get caret coordinates in a contenteditable
      when the collapsed-range rect returns zeroes (common in iframes). */
	function getCaretRect() {
		var sel = document.getSelection();
		if (!sel || sel.rangeCount === 0) return null;
		var range = sel.getRangeAt(0).cloneRange();
		range.collapse(true);
		var marker = document.createElement('span');
		marker.textContent = '​';
		range.insertNode(marker);
		var rect = marker.getBoundingClientRect();
		var parent = marker.parentNode;
		if (parent) {
			parent.removeChild(marker);
			parent.normalize();
		}
		return rect;
	}

	document.addEventListener(
		'input',
		function (e) {
			var target = e.target;
			if (!target || target.contentEditable !== 'true') {
				hideDropdown();
				return;
			}
			var ctx = getCaretContext();
			if (!ctx) {
				hideDropdown();
				return;
			}
			var rect = getCaretRect();
			if (!rect) {
				hideDropdown();
				return;
			}
			showDropdown(ctx, rect);
		},
		true
	);

	document.addEventListener(
		'keydown',
		function (e) {
			if (!dropdown) return;
			var items = dropdown.querySelectorAll('[data-ac-item]');
			if (e.key === 'Escape') {
				e.stopPropagation();
				hideDropdown();
			} else if (e.key === 'ArrowDown') {
				e.preventDefault();
				setActive(Math.min(activeIndex + 1, items.length - 1));
			} else if (e.key === 'ArrowUp') {
				e.preventDefault();
				setActive(Math.max(activeIndex - 1, 0));
			} else if (e.key === 'Enter' || e.key === 'Tab') {
				if (activeIndex >= 0 && items[activeIndex]) {
					e.preventDefault();
					e.stopPropagation();
					var it = items[activeIndex];
					insertCompletion(it.getAttribute('data-ac-value'), it.getAttribute('data-ac-mode'));
				}
			}
		},
		true
	);

	document.addEventListener(
		'click',
		function (e) {
			if (dropdown && !dropdown.contains(e.target)) hideDropdown();
		},
		true
	);

	// ── Editor-only token highlight overlay ───────────────────────────────────
	// Paint a subtle tint behind each {{ token }} using Range rects — NO DOM
	// wrapping, so it can't fragment flex text runs or leak into the exported HTML.
	// Any {{ … }} token — including ones with filters/args (e.g. {{ x | date('%Y') }}).
	// [^{}] stops at the first }}, so adjacent tokens aren't merged.
	var TOKEN_RE = /\{\{[^{}]*\}\}/g;
	// The chip's two greys, the same values the email editor's highlighter uses. Kept as
	// constants so the pair is read as one decision, and so a change here is a change there.
	var CHIP_FILL = '#e9ebf0';
	var CHIP_RING = '#ccd2e0';
	var hlRoot = null;
	var hlOn = true; // toggled off in preview via postMessage
	var hlTimer = null;
	var hlObserver = null;

	function clearHl() {
		if (hlRoot && hlRoot.parentNode) hlRoot.parentNode.removeChild(hlRoot);
		hlRoot = null;
	}

	function drawHl() {
		// Detach the observer around our own DOM writes so appending the overlay
		// doesn't re-trigger a redraw (infinite loop).
		if (hlObserver) hlObserver.disconnect();
		clearHl();
		if (hlOn) {
			var br = document.body.getBoundingClientRect();
			var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
			var frag = document.createDocumentFragment();
			var any = false;
			var node;
			while ((node = walker.nextNode())) {
				var text = node.textContent;
				if (!text || text.indexOf('{{') === -1) continue;
				var p = node.parentNode;
				if (p && p.closest && p.closest('#gjs-jinja-hl,[role="listbox"]')) continue; // skip overlay/dropdown
				TOKEN_RE.lastIndex = 0;
				var m;
				while ((m = TOKEN_RE.exec(text))) {
					var range = document.createRange();
					range.setStart(node, m.index);
					range.setEnd(node, m.index + m[0].length);
					var rects = range.getClientRects();
					for (var i = 0; i < rects.length; i++) {
						var r = rects[i];
						if (!r.width || !r.height) continue;
						var d = document.createElement('div');
						// THE EMAIL EDITOR'S CHIP, to the value (`gjs-email-jinja-hl.js`), and its reasoning
						// holds here unchanged: a HIGHLIGHTER, not an overprint. The violet wash this replaces
						// was the same defect that one measured before it was fixed — a chip whose own fill
						// drops the contrast of the text inside it (1.19:1 against 2.60:1 for un-chipped copy).
						// Two neutral greys plus `multiply`, which can only DARKEN, so the backdrop dims and
						// dark text does not: a token reads as marked rather than as faded. Neutral also keeps
						// it out of the way of the blue/amber/green box-model overlays, which is what the
						// violet was for.
						d.style.cssText =
							'position:absolute;pointer-events:none;border-radius:3px;' +
							'background:' +
							CHIP_FILL +
							';box-shadow:inset 0 0 0 1px ' +
							CHIP_RING +
							';' +
							'mix-blend-mode:multiply;' +
							'left:' +
							(r.left - br.left - 1) +
							'px;top:' +
							(r.top - br.top - 1) +
							'px;' +
							'width:' +
							(r.width + 2) +
							'px;height:' +
							(r.height + 2) +
							'px;';
						frag.appendChild(d);
						any = true;
					}
				}
			}
			if (any) {
				hlRoot = document.createElement('div');
				hlRoot.id = 'gjs-jinja-hl';
				// NO NUMERIC Z-INDEX. A numeric one makes a STACKING CONTEXT and `mix-blend-mode` blends
				// only within its own, so the chips would composite against this layer's transparent
				// backdrop and paint as flat opaque GREY over the token — worse than no highlight at all.
				// `z-index: auto` makes no context, and the last positioned child of the body wins, which
				// is this layer. Same rule, same reason, as the email editor's overlay.
				// `pointer-events:none` so clicks pass through to the text for editing.
				hlRoot.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;';
				hlRoot.appendChild(frag);
				document.body.appendChild(hlRoot);
			}
		}
		if (hlObserver)
			hlObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
	}

	function scheduleHl() {
		if (hlTimer) return;
		hlTimer = setTimeout(function () {
			hlTimer = null;
			drawHl();
		}, 60);
	}

	function initHl() {
		hlObserver = new MutationObserver(scheduleHl);
		drawHl();
		window.addEventListener('resize', scheduleHl, { passive: true });
		document.addEventListener('scroll', scheduleHl, true);
		document.addEventListener('input', scheduleHl, true);
		document.addEventListener('focusout', scheduleHl, true);
		// Canvas scrolls via the parent container; getClientRects shift with it.
		try {
			var cv = window.parent.document.querySelector('.gjs-cv-canvas');
			if (cv) cv.addEventListener('scroll', scheduleHl, { passive: true });
		} catch (e) {}
	}

	// ── Page-overflow detection ───────────────────────────────────────────────
	// Each `.page` has a single-page min-height (mm → px in the unzoomed iframe). When its content grows
	// taller than that, the generated PDF will split it across pages — so tell the parent, which shows an
	// in-editor overflow indicator (rather than the author only finding out at preview/generation time).
	var ovLast = '';
	var ovTimer = null;
	function measureOverflow() {
		var pages = document.querySelectorAll('.page');
		var out = [];
		for (var i = 0; i < pages.length; i++) {
			var p = pages[i];
			var minH = parseFloat(getComputedStyle(p).minHeight) || 0;
			// scrollHeight is the content box+padding; compare to the single-page floor with a small
			// tolerance so a page that exactly fills one sheet isn't flagged.
			var over = minH > 0 ? Math.max(0, p.scrollHeight - Math.round(minH)) : 0;
			out.push({ index: i, overflow: over > 4, over: over });
		}
		var sig = JSON.stringify(
			out.map(function (o) {
				return o.overflow ? 1 : 0;
			})
		);
		if (sig === ovLast) return; // only post on change
		ovLast = sig;
		try {
			window.parent.postMessage({ type: 'gjs-page-overflow', pages: out }, '*');
		} catch (e) {}
	}
	function scheduleOverflow() {
		if (ovTimer) return;
		ovTimer = setTimeout(function () {
			ovTimer = null;
			measureOverflow();
		}, 120);
	}
	function initOverflow() {
		new MutationObserver(scheduleOverflow).observe(document.body, {
			childList: true,
			subtree: true,
			characterData: true,
			attributes: true
		});
		window.addEventListener('resize', scheduleOverflow, { passive: true });
		document.addEventListener('input', scheduleOverflow, true);
		measureOverflow();
	}

	window.addEventListener('message', function (e) {
		if (e.data && e.data.type === 'gjs-jinja-hl') {
			hlOn = !!e.data.on;
			drawHl();
		}
	});

	function initAll() {
		initHl();
		initOverflow();
	}
	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
	else initAll();
})();
