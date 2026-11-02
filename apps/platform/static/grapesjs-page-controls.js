/**
 * Page control buttons for the GrapesJS canvas.
 *
 * Renders "+" buttons in the gap between adjacent pages and below the last page,
 * centred horizontally on the page.
 *
 * Clicking a button sends:
 *   window.parent.postMessage({ type: 'gjs-add-page', afterIndex: N }, '*')
 *
 * The parent React app inserts a new .page div after index N.
 */
(function () {
  'use strict';

  var BTN_SIZE = 24;   // px
  var GAP = 32;        // matches .page margin-bottom in canvas CSS

  var controlRoot = null;

  function removeControls() {
    if (controlRoot) { controlRoot.remove(); controlRoot = null; }
  }

  function makeBtn(x, y, afterIndex) {
    var btn = document.createElement('button');
    btn.setAttribute('data-pg-ctrl', '1');
    btn.title = 'Add page here';
    btn.textContent = '+';
    btn.style.cssText = [
      'position:absolute',
      'left:' + x + 'px',
      'top:' + y + 'px',
      'width:' + BTN_SIZE + 'px',
      'height:' + BTN_SIZE + 'px',
      'border-radius:50%',
      'border:2px solid #94a3b8',
      'background:#fff',
      'color:#64748b',
      'font-size:16px',
      'line-height:1',
      'cursor:pointer',
      'display:flex',
      'align-items:center',
      'justify-content:center',
      'z-index:9997',
      'box-shadow:0 1px 4px rgba(0,0,0,0.12)',
      'pointer-events:all',
    ].join(';');

    btn.addEventListener('mousedown', function (e) {
      e.stopImmediatePropagation();
      e.preventDefault();
      window.parent.postMessage({ type: 'gjs-add-page', afterIndex: afterIndex }, '*');
    }, true);
    return btn;
  }

  function buildControls() {
    removeControls();
    var pages = Array.from(document.querySelectorAll('.page'));
    if (!pages.length) return;

    controlRoot = document.createElement('div');
    controlRoot.id = 'gjs-page-ctrl-root';
    controlRoot.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:9996;';

    var bodyRect = document.body.getBoundingClientRect();

    pages.forEach(function (page, idx) {
      var r = page.getBoundingClientRect();
      var absTop = r.top - bodyRect.top;   // top of this .page div in document coords
      var absLeft = r.left - bodyRect.left;

      // Centred horizontally on the page, in the gap below this page
      var bx = absLeft + r.width / 2 - BTN_SIZE / 2;
      var by = absTop + r.height + (GAP - BTN_SIZE) / 2;
      controlRoot.appendChild(makeBtn(bx, by, idx));
    });

    document.body.appendChild(controlRoot);
  }

  // Rebuild when .page elements are added/removed (but not when the ctrl root itself changes
  // — watching gjs-page-ctrl-root would cause an infinite rebuild loop).
  new MutationObserver(function (mutations) {
    var relevant = mutations.some(function (m) {
      return Array.from(m.addedNodes).concat(Array.from(m.removedNodes)).some(function (n) {
        return n.nodeType === 1 && n.classList && n.classList.contains('page');
      });
    });
    if (relevant) setTimeout(buildControls, 50);
  }).observe(document.body, { childList: true, subtree: false });

  try {
    var canvas = window.parent.document.querySelector('.gjs-cv-canvas');
    if (canvas) canvas.addEventListener('scroll', buildControls, { passive: true });
  } catch (e) {}

  // Also rebuild when the iframe itself scrolls (affects getBoundingClientRect values)
  document.addEventListener('scroll', buildControls, { passive: true });

  document.addEventListener('DOMContentLoaded', buildControls);
  setTimeout(buildControls, 300); // initial build after GJS renders
})();
