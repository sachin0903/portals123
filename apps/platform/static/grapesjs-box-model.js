/**
 * Box model visual overlay for GrapesJS canvas.
 *
 * At rest the element is NOT bordered — instead each editable dimension gets a
 * small colour-coded drag tab:
 *   orange = margin   (tabs sit outside the element)
 *   blue   = size     (tabs sit on the element's border box)
 *   green  = padding  (tabs sit on the content box)
 * Hovering or dragging a tab reveals the region it affects (orange margin ring,
 * blue size box, or green padding ring) so it's clear what's changing.
 *
 * Uses position:absolute (NOT fixed) with body-relative coordinates so the
 * overlay stays with its element even when the parent canvas container scrolls.
 * position:fixed inside an iframe uses the iframe's visual viewport, which
 * shifts when the parent .gjs-cv-canvas scrolls — causing the overlay to drift.
 */
(function () {
  'use strict';

  var T   = 8;    // size-edge grab-strip thickness
  var TT  = 10;   // margin/padding tab thickness — kept > T (and above it in z-order) so the
                  //   orange/green tabs win the overlap zone and you can't click past them
                  //   onto the blue size edge behind.
  var TAB = 20;   // tab length
  var S   = 26;   // stagger between the tabs on one edge

  // Per-region palette. `line`/`hi` are the region outline + fill shown while a
  // tab is active; `tab`/`tabHi` are the drag tab's own colour.
  var C = {
    margin:  { line: 'rgba(245,158,11,0.9)', hi: 'rgba(245,158,11,0.30)', tab: 'rgba(249,194,102,0.9)', tabHi: 'rgba(245,158,11,0.95)' },
    // Padding: a soft, pale sage green — the fill lands around rgb(212,228,217) over white,
    // a calm professional tint distinct from the blue size box and the teal brand.
    padding: { line: 'rgba(90,150,110,0.85)', hi: 'rgba(120,170,135,0.32)', tab: 'rgba(150,195,165,0.95)', tabHi: 'rgba(90,150,110,0.95)' },
    // The size box stays faintly visible at rest (restLine); its tab brightens it.
    size:    { line: 'rgba(59,130,246,0.95)',hi: 'rgba(59,130,246,0.14)', tab: 'rgba(59,130,246,0.9)', tabHi: 'rgb(59,130,246)', restLine: 'rgba(59,130,246,0.55)' }
  };

  var root   = null;
  var selEl  = null;
  var drag   = null;
  var tabEls = [];                                       // margin/padding tabs (fade on element hover)
  var visuals = { margin: [], padding: [], size: [] };   // hidden-at-rest region overlays
  var hoverRoot = null;                                  // read-only spacing overlay for the hovered (unselected) element
  var hoverEl   = null;

  // Fade the margin/padding tabs in only when the pointer is near the element,
  // so they stay unobtrusive while reading/editing.
  function setTabsVisible(on) {
    for (var i = 0; i < tabEls.length; i++) tabEls[i].style.opacity = on ? '1' : '0';
  }

  function px(n) { return n + 'px'; }

  function box(el) {
    var cs = window.getComputedStyle(el);
    return {
      mt: parseFloat(cs.marginTop)    || 0,
      mr: parseFloat(cs.marginRight)  || 0,
      mb: parseFloat(cs.marginBottom) || 0,
      ml: parseFloat(cs.marginLeft)   || 0,
      pt: parseFloat(cs.paddingTop)   || 0,
      pr: parseFloat(cs.paddingRight) || 0,
      pb: parseFloat(cs.paddingBottom)|| 0,
      pl: parseFloat(cs.paddingLeft)  || 0
    };
  }

  function prop(cssProp) {
    if (!selEl) return 0;
    return parseFloat(window.getComputedStyle(selEl)[cssProp.replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); })]) || 0;
  }

  // Get element rect relative to document body (absolute, scroll-independent)
  function absRect(el) {
    var r = el.getBoundingClientRect();
    var br = document.body.getBoundingClientRect();
    return {
      top:    r.top    - br.top,
      left:   r.left   - br.left,
      width:  r.width,
      height: r.height,
      right:  r.left - br.left + r.width,
      bottom: r.top  - br.top  + r.height
    };
  }

  // A region outline rectangle — invisible until its tab is engaged.
  function addOutline(kind, x, y, w, h, z) {
    var d = document.createElement('div');
    d.style.cssText = 'position:absolute;pointer-events:none;background:transparent;z-index:' + z +
      ';outline:1px dashed transparent;outline-offset:-1px;transition:outline-color .12s,background .12s' +
      ';left:' + px(x) + ';top:' + px(y) + ';width:' + px(w) + ';height:' + px(h);
    d._kind = kind;
    d._outline = true;
    // The size box is always faintly visible; margin/padding stay hidden at rest.
    if (kind === 'size') d.style.outlineColor = C.size.restLine;
    visuals[kind].push(d);
    root.appendChild(d);
  }

  // A region fill strip (part of a margin/padding ring) — transparent until engaged.
  function addStrip(kind, x, y, w, h) {
    if (w <= 0 || h <= 0) return;
    var d = document.createElement('div');
    d.style.cssText = 'position:absolute;pointer-events:none;background:transparent;z-index:9998;transition:background .12s' +
      ';left:' + px(x) + ';top:' + px(y) + ';width:' + px(w) + ';height:' + px(h);
    d._kind = kind;
    visuals[kind].push(d);
    root.appendChild(d);
  }

  // Reveal (or hide) a region's outline + fill.
  function highlight(kind, on) {
    (visuals[kind] || []).forEach(function (d) {
      if (d._outline) {
        // Size keeps a faint outline at rest; margin/padding hide entirely.
        d.style.outlineColor = on ? C[kind].line : (kind === 'size' ? C.size.restLine : 'transparent');
        if (kind === 'size') d.style.background = on ? C.size.hi : 'transparent';
      } else {
        d.style.background = on ? C[kind].hi : 'transparent';
      }
    });
  }

  // Begin a drag on `el`, tracking which region/prop/axis is being changed.
  // `invert` flips the drag axis so dragging away from the content increases it.
  function startDrag(el, kind, cssProp, side, invert, e) {
    e.stopImmediatePropagation();
    e.preventDefault();
    var startV;
    if (cssProp === 'width'  && selEl) startV = selEl.getBoundingClientRect().width;
    else if (cssProp === 'height' && selEl) startV = selEl.getBoundingClientRect().height;
    else startV = prop(cssProp);
    drag = {
      el: el, kind: kind, prop: cssProp, side: side,
      startX: e.clientX, startY: e.clientY,
      startVal: startV,
      vert: side === 'top' || side === 'bottom',
      invert: invert
    };
    highlight(kind, true);
  }

  // A draggable margin/padding tab — a small coloured pill.
  function tab(kind, cssProp, side, invert, x, y, w, h) {
    var cursor = (side === 'top' || side === 'bottom') ? 'ns-resize' : 'ew-resize';
    var el = document.createElement('div');
    el.setAttribute('data-bm', '1');
    el.style.cssText = 'position:absolute;pointer-events:all;box-sizing:border-box;opacity:0;cursor:' + cursor +
      ';z-index:10001;background:' + C[kind].tab + ';border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.25)' +
      ';transition:background .12s,transform .1s,opacity .12s' +
      ';left:' + px(x) + ';top:' + px(y) + ';width:' + px(w) + ';height:' + px(h);
    tabEls.push(el);

    el.addEventListener('mouseenter', function () {
      if (drag) return;
      el.style.background = C[kind].tabHi;
      el.style.transform = 'scale(1.15)';
      highlight(kind, true);
    });
    el.addEventListener('mouseleave', function () {
      if (drag && drag.el === el) return;
      el.style.background = C[kind].tab;
      el.style.transform = 'none';
      highlight(kind, false);
    });
    el.addEventListener('mousedown', function (e) { startDrag(el, kind, cssProp, side, invert, e); }, true);
    return el;
  }

  // A full-length transparent grab strip along one edge of the size box, so the
  // whole blue outline is draggable (not just discrete points).
  function edge(cssProp, side, invert, x, y, w, h) {
    var cursor = (side === 'top' || side === 'bottom') ? 'ns-resize' : 'ew-resize';
    var el = document.createElement('div');
    el.setAttribute('data-bm', '1');
    el.style.cssText = 'position:absolute;pointer-events:all;background:transparent;cursor:' + cursor +
      ';z-index:10000' + // below the margin/padding tabs (10001) so those stay grabbable
      ';left:' + px(x) + ';top:' + px(y) + ';width:' + px(w) + ';height:' + px(h);
    el.addEventListener('mouseenter', function () { if (!drag) highlight('size', true); });
    el.addEventListener('mouseleave', function () { if (!drag || drag.el !== el) highlight('size', false); });
    el.addEventListener('mousedown', function (e) { startDrag(el, 'size', cssProp, side, invert, e); }, true);
    return el;
  }

  function build(el) {
    clear();
    selEl = el;
    // A page's margin is expressed as padding (applyPageLayout) and the sheet is auto-centred,
    // so its CSS margin isn't meaningful to edit — hide the margin ring + tabs for pages and
    // let the padding tabs stand in for the page margin.
    var isPage = !!(el.classList && el.classList.contains('page'));
    var b = box(el);
    var r = absRect(el);   // body-relative, scroll-independent

    // Margin (outer) box + content (inner) box
    var mx = r.left - b.ml, my = r.top - b.mt;
    var mw = r.width + b.ml + b.mr, mh = r.height + b.mt + b.mb;
    var cx = r.left + b.pl, cy = r.top + b.pt;
    var cw = r.width - b.pl - b.pr, ch = r.height - b.pt - b.pb;

    // Root is position:absolute at body origin so all children use body coords
    root = document.createElement('div');
    root.id = 'gjs-bm';
    root.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:9998;';

    // ── Region overlays (hidden until a tab is engaged) ───────────────────
    // Margin ring (skipped for pages)
    if (!isPage) {
      addOutline('margin', mx, my, mw, mh, 9998);
      addStrip('margin', r.left,  my,       r.width, b.mt);
      addStrip('margin', r.right, r.top,    b.mr,    r.height);
      addStrip('margin', r.left,  r.bottom, r.width, b.mb);
      addStrip('margin', mx,      r.top,    b.ml,    r.height);
    }
    // Size box
    addOutline('size', r.left, r.top, r.width, r.height, 9999);
    // Padding ring. Top/bottom span the full width and own the corners; left/right fill
    // only the inner height, so the semi-transparent strips don't overlap (darken) at corners.
    if (cw > 0 && ch > 0) {
      addOutline('padding', cx, cy, cw, ch, 10000);
      addStrip('padding', r.left,  r.top,   r.width, b.pt);
      addStrip('padding', r.left,  cy + ch, r.width, b.pb);
      addStrip('padding', cx + cw, cy,      b.pr,    ch);
      addStrip('padding', r.left,  cy,      b.pl,    ch);
    }

    // ── Size — the whole blue outline is grabbable (no discrete tabs) ─────
    root.appendChild(edge('height', 'top',    true,  r.left,          r.top - T / 2,    r.width, T));
    root.appendChild(edge('height', 'bottom', false, r.left,          r.bottom - T / 2, r.width, T));
    root.appendChild(edge('width',  'left',   true,  r.left - T / 2,  r.top,            T, r.height));
    root.appendChild(edge('width',  'right',  false, r.right - T / 2, r.top,            T, r.height));

    // ── Margin (orange) + padding (green) tabs, staggered near each edge's
    //    midpoint. z 10001 keeps them grabbable on top of the size edges. ──
    var cX = r.left + r.width / 2, cY = r.top + r.height / 2;
    function hx(offset) { return cX + offset - TAB / 2; } // horizontal-tab x
    function vy(offset) { return cY + offset - TAB / 2; } // vertical-tab y

    // Margin tabs are omitted for pages; padding tabs (the page margin) are centred on the edge.
    // Top
    if (!isPage) root.appendChild(tab('margin',  'margin-top',    'top', true,  hx(-S / 2), my - TT / 2, TAB, TT));
    root.appendChild(tab('padding', 'padding-top',   'top', false, hx(isPage ? 0 : S / 2),  cy - TT / 2, TAB, TT));
    // Bottom
    if (!isPage) root.appendChild(tab('margin',  'margin-bottom', 'bottom', false, hx(-S / 2), my + mh - TT / 2, TAB, TT));
    root.appendChild(tab('padding', 'padding-bottom','bottom', true,  hx(isPage ? 0 : S / 2),  cy + ch - TT / 2, TAB, TT));
    // Left
    if (!isPage) root.appendChild(tab('margin',  'margin-left',   'left', true,  mx - TT / 2, vy(-S / 2), TT, TAB));
    root.appendChild(tab('padding', 'padding-left',  'left', false, cx - TT / 2, vy(isPage ? 0 : S / 2),  TT, TAB));
    // Right
    if (!isPage) root.appendChild(tab('margin',  'margin-right',  'right', false, mx + mw - TT / 2, vy(-S / 2), TT, TAB));
    root.appendChild(tab('padding', 'padding-right', 'right', true,  cx + cw - TT / 2, vy(isPage ? 0 : S / 2),  TT, TAB));

    document.body.appendChild(root);

    // Keep the active region lit through the rebuilds that happen mid-drag.
    if (drag) highlight(drag.kind, true);
  }

  function clear() {
    if (root) { root.remove(); root = null; }
    visuals = { margin: [], padding: [], size: [] };
    tabEls = [];
    selEl = null;
  }

  // ── Hover spacing overlay ──────────────────────────────────────────────────
  // A read-only margin (orange) + padding (green) view shown while a component is
  // hovered but not selected — the classic dev-tools spacing overlay. No drag tabs
  // and pointer-events:none, so it never interferes with clicking/selecting.
  function clearHover() {
    if (hoverRoot) { hoverRoot.remove(); hoverRoot = null; }
    hoverEl = null;
  }

  function buildHover(el) {
    clearHover();
    hoverEl = el;
    var b = box(el);
    var r = absRect(el);   // body-relative, scroll-independent (same basis as build())
    hoverRoot = document.createElement('div');
    hoverRoot.id = 'gjs-bm-hover';
    hoverRoot.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:9997;';
    function strip(x, y, w, h, color) {
      if (w <= 0 || h <= 0) return;
      var d = document.createElement('div');
      d.style.cssText = 'position:absolute;pointer-events:none;background:' + color +
        ';left:' + px(x) + ';top:' + px(y) + ';width:' + px(w) + ';height:' + px(h);
      hoverRoot.appendChild(d);
    }
    // Margin ring (outside the border box) — mirrors build()'s strip geometry.
    strip(r.left,        r.top - b.mt, r.width, b.mt,     C.margin.hi);
    strip(r.right,       r.top,        b.mr,    r.height,  C.margin.hi);
    strip(r.left,        r.bottom,     r.width, b.mb,      C.margin.hi);
    strip(r.left - b.ml, r.top,        b.ml,    r.height,  C.margin.hi);
    // Padding ring (inside the border box). Top/bottom span the full width and own the
    // corners; left/right fill only the inner height — so the semi-transparent strips
    // never overlap and double up (darken) at the corners.
    var cw = r.width - b.pl - b.pr, ch = r.height - b.pt - b.pb;
    strip(r.left,             r.top,             r.width, b.pt, C.padding.hi);
    strip(r.left,             r.top + b.pt + ch, r.width, b.pb, C.padding.hi);
    strip(r.left + b.pl + cw, r.top + b.pt,      b.pr,    ch,   C.padding.hi);
    strip(r.left,             r.top + b.pt,      b.pl,    ch,   C.padding.hi);
    document.body.appendChild(hoverRoot);
  }

  // Track the hovered component (GrapesJS adds .gjs-hovered to its element).
  function checkHover() {
    if (drag) { clearHover(); return; }
    var hov = document.querySelector('.gjs-hovered');
    // The selected element already carries the full overlay — don't double up.
    if (hov && hov.classList.contains('gjs-selected')) hov = null;
    if (hov === hoverEl) return;
    if (hov) buildHover(hov); else clearHover();
  }

  // Reveal the tabs only while the pointer is within the element's margin box
  // (plus a little tolerance so the outer margin tabs stay reachable).
  document.addEventListener('mousemove', function (e) {
    if (!selEl || !root) return;
    if (drag) { setTabsVisible(true); return; }
    var rc = selEl.getBoundingClientRect();
    var cs = window.getComputedStyle(selEl);
    var ml = parseFloat(cs.marginLeft)  || 0, mr = parseFloat(cs.marginRight)  || 0;
    var mt = parseFloat(cs.marginTop)   || 0, mb = parseFloat(cs.marginBottom) || 0;
    var pad = 10;
    var inside = e.clientX >= rc.left - ml - pad && e.clientX <= rc.right + mr + pad &&
                 e.clientY >= rc.top - mt - pad  && e.clientY <= rc.bottom + mb + pad;
    setTabsVisible(inside);
  }, { passive: true });

  // Drag handling. Intermediate moves are marked phase:'move' so the parent can
  // apply them without touching the undo stack; the phase:'end' message on mouseup
  // commits the whole gesture as a single undo step (see the parent's onMessage).
  document.addEventListener('mousemove', function (e) {
    if (!drag) return;
    var raw   = drag.vert ? (e.clientY - drag.startY) : (e.clientX - drag.startX);
    var delta = drag.invert ? -raw : raw;
    var val   = Math.max(0, Math.round(drag.startVal + delta));
    drag.lastVal = val;
    window.parent.postMessage({ type: 'gjs-box-model', prop: drag.prop, value: val + 'px', phase: 'move' }, '*');
    if (selEl) build(selEl);
  }, true);

  function endDrag() {
    if (!drag) return;
    var d = drag;
    drag = null;
    // Only commit if the drag actually moved (a bare click sets no lastVal).
    if (d.lastVal != null) {
      window.parent.postMessage({ type: 'gjs-box-model', prop: d.prop, value: d.lastVal + 'px', phase: 'end' }, '*');
    }
    if (selEl) setTimeout(function () { build(selEl); }, 60);
  }
  document.addEventListener('mouseup', endDrag, true);
  // Release outside the iframe (pointer left the frame): still commit the single undo step.
  window.addEventListener('blur', endDrag);

  // Selection watcher
  // Text tags: skip overlay entirely — resize handles intercept the second click
  // that activates GJS's RTE, making inline text editing impossible.
  var NO_OVERLAY_TAGS = new Set(['H1','H2','H3','H4','H5','H6','P','UL','OL','LI','BLOCKQUOTE','SPAN','A','STRONG','EM']);

  var lastSel = null;
  function checkSel() {
    // No overlay when multiple components are selected
    if (document.querySelectorAll('.gjs-selected').length > 1) {
      if (lastSel) { clear(); lastSel = null; }
      return;
    }
    var sel = document.querySelector('.gjs-selected');
    if (sel === lastSel) return;
    lastSel = sel;
    if (sel && NO_OVERLAY_TAGS.has((sel.tagName || '').toUpperCase())) {
      clear(); return; // no overlay for text elements
    }
    if (sel) build(sel); else clear();
  }

  new MutationObserver(function () { checkSel(); checkHover(); }).observe(document.body, {
    subtree: true, attributeFilter: ['class'], childList: true
  });

  // Hide overlay while the RTE toolbar is visible (text editing mode) so tabs
  // don't intercept clicks that should activate or operate the RTE.
  var rteObserver = new MutationObserver(function () {
    var rteActive = !!document.querySelector('.gjs-rte-toolbar');
    if (root) root.style.display = rteActive ? 'none' : '';
    if (hoverRoot) hoverRoot.style.display = rteActive ? 'none' : '';
  });
  rteObserver.observe(document.body, { childList: true, subtree: true });

  // Rebuild on parent canvas scroll — even though we use absolute positioning,
  // getBoundingClientRect() values change with parent scroll so we must rebuild.
  try {
    var canvas = window.parent.document.querySelector('.gjs-cv-canvas');
    if (canvas) canvas.addEventListener('scroll', function () {
      if (selEl) build(selEl);
      if (hoverEl) buildHover(hoverEl);
    }, { passive: true });
  } catch (e) {}

  // Hook called by the parent panel after any addStyle() so the overlay
  // re-reads getComputedStyle immediately (GrapesJS applies inline styles synchronously).
  window.__gjsBmRebuild = function () { if (selEl) build(selEl); };

  document.addEventListener('DOMContentLoaded', checkSel);
  checkSel();
})();
