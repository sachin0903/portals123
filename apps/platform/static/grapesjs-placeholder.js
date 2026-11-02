/**
 * Placeholder behaviour for GrapesJS text blocks.
 *
 * Elements with [data-tmpl-ph] appear greyed-out/italic on first drop
 * (via grapesjs-canvas.css). While that attribute is present, entering edit
 * mode always selects all content so the user can type to replace immediately —
 * like clicking a URL bar. Once the user types, [data-tmpl-ph] is removed and
 * the block behaves like normal text on all subsequent edits.
 *
 * Two complementary strategies for reliable selection:
 *  1. MutationObserver on contenteditable — fires when GrapesJS enters edit mode,
 *     deferred via setTimeout past GrapesJS's own cursor-placement handlers.
 *  2. keydown fallback — if the deferred select-all was still overridden, catch
 *     the first printable key and force selectAll before it's inserted.
 */
(function () {
  'use strict';

  function selectAll(el) {
    try {
      var sel = window.getSelection();
      var range = document.createRange();
      range.selectNodeContents(el);
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (_) {}
  }

  // Watch for GrapesJS setting contenteditable="true". When the element (or an
  // ancestor) still has [data-tmpl-ph], select-all so typing replaces the placeholder.
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mut) {
        if (mut.attributeName !== 'contenteditable') return;
        var el = mut.target;
        if (!el || el.contentEditable !== 'true') return;
        // Only act on unedited placeholder elements.
        var phEl = el.hasAttribute && el.hasAttribute('data-tmpl-ph')
          ? el
          : (el.closest ? el.closest('[data-tmpl-ph]') : null);
        if (!phEl) return;
        var captured = phEl;
        // Defer past GrapesJS's own mouseup / rte:enable cursor-placement handlers.
        setTimeout(function () {
          var sel = window.getSelection();
          if (sel && captured.contains(sel.focusNode)) selectAll(captured);
        }, 0);
      });
    }).observe(document.body, {
      attributes: true,
      attributeFilter: ['contenteditable'],
      subtree: true
    });
  }

  // Belt-and-suspenders: if GrapesJS re-positioned the cursor after our setTimeout,
  // catch the first printable key in a [data-tmpl-ph] element and force selectAll
  // before the character is inserted.
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1) return;
    var el = e.target;
    if (!el) return;
    var phEl = el.hasAttribute && el.hasAttribute('data-tmpl-ph')
      ? el
      : (el.closest ? el.closest('[data-tmpl-ph]') : null);
    if (!phEl) return;
    var sel = window.getSelection();
    if (sel && sel.isCollapsed) selectAll(phEl);
  }, true);

  // On first input, strip [data-tmpl-ph] so the text reverts to normal styling
  // and subsequent edits get regular cursor placement. Also notify the parent
  // frame so the GrapesJS model attribute is cleaned up (preventing it from
  // re-appearing in the DOM after a save/reload cycle).
  document.addEventListener('input', function (e) {
    var el = e.target;
    if (!el) return;
    // Self or ancestor (simple text blocks and highlight boxes)
    var target = el.hasAttribute && el.hasAttribute('data-tmpl-ph')
      ? el
      : (el.closest ? el.closest('[data-tmpl-ph]') : null);
    if (target) {
      target.removeAttribute('data-tmpl-ph');
      try { window.parent.postMessage({ type: 'gjs-tmpl-ph-removed' }, '*'); } catch (_) {}
      return;
    }
    // Descendants (composite blocks: label+value, note boxes, lists)
    if (el.querySelectorAll) {
      var children = el.querySelectorAll('[data-tmpl-ph]');
      if (children.length) {
        for (var i = 0; i < children.length; i++) {
          children[i].removeAttribute('data-tmpl-ph');
        }
        try { window.parent.postMessage({ type: 'gjs-tmpl-ph-removed' }, '*'); } catch (_) {}
      }
    }
  }, true);
})();
