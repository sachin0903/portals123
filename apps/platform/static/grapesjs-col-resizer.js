/**
 * Column resizer for GrapesJS canvas.
 * Injects a drag handle between adjacent .gjs-cell elements in horizontal
 * .gjs-row containers. On drop, posts the updated flex-basis values to the
 * parent window so the GrapesJS component model can persist them.
 */
(function () {
  'use strict';

  var drag = null;

  function getHCells(row) {
    if (window.getComputedStyle(row).flexDirection === 'column') return [];
    return Array.from(row.children).filter(function (el) {
      return el.classList.contains('gjs-cell');
    });
  }

  function makeHandle(row, cells, idx) {
    var handle = document.createElement('div');
    handle.setAttribute('data-col-handle', String(idx));
    handle.style.cssText =
      'position:absolute;top:0;bottom:0;width:12px;cursor:col-resize;' +
      'z-index:9999;display:flex;align-items:center;justify-content:center;';

    var pip = document.createElement('div');
    pip.style.cssText =
      'width:2px;border-radius:1px;height:calc(100% - 12px);' +
      'background:transparent;pointer-events:none;transition:background .12s;';
    handle.appendChild(pip);

    handle.addEventListener('mouseenter', function () {
      if (!drag) pip.style.background = 'rgba(99,102,241,.45)';
    });
    handle.addEventListener('mouseleave', function () {
      if (!drag) pip.style.background = 'transparent';
    });

    // Attach data directly on the element so the capture listener can read it
    handle.__pip    = pip;
    handle.__cells  = cells;
    handle.__row    = row;
    handle.__idx    = idx;

    row.appendChild(handle);

    function reposition() {
      var rr = row.getBoundingClientRect();
      var cr = cells[idx].getBoundingClientRect();
      handle.style.left = (cr.right - rr.left - 6) + 'px';
    }

    reposition();
    var ro = new ResizeObserver(reposition);
    ro.observe(cells[idx]);
    ro.observe(row);
  }

  function setupRow(row) {
    if (row.__colResizerReady) return;
    row.__colResizerReady = true;
    row.style.position = 'relative';

    var cells = getHCells(row);
    for (var i = 0; i < cells.length - 1; i++) {
      makeHandle(row, cells, i);
    }
  }

  // Capture-phase: fires before GrapesJS bubble-phase listeners
  document.addEventListener('mousedown', function (e) {
    var target = e.target;
    var handle = target.closest ? target.closest('[data-col-handle]') : null;
    if (!handle) return;

    var cells = handle.__cells;
    if (!cells) return;

    e.stopImmediatePropagation();
    e.preventDefault();

    handle.__pip.style.background = 'rgba(99,102,241,.8)';

    var rowW  = handle.__row.getBoundingClientRect().width;
    var startA = cells[handle.__idx].getBoundingClientRect().width;
    var startB = cells[handle.__idx + 1].getBoundingClientRect().width;

    drag = {
      cells:  cells,
      row:    handle.__row,
      idx:    parseInt(handle.getAttribute('data-col-handle'), 10),
      pip:    handle.__pip,
      startX: e.clientX,
      rowW:   rowW,
      startA: startA,
      startB: startB,
      totalPct: (startA + startB) / rowW * 100,
    };

    // Lock current widths immediately so there is no jump before the first mousemove
    var pA0 = (startA / rowW * 100).toFixed(3) + '%';
    var pB0 = (startB / rowW * 100).toFixed(3) + '%';
    cells[drag.idx].style.flexBasis    = pA0;
    cells[drag.idx].style.flexGrow     = '0';
    cells[drag.idx].style.flexShrink   = '1';
    cells[drag.idx + 1].style.flexBasis  = pB0;
    cells[drag.idx + 1].style.flexGrow   = '0';
    cells[drag.idx + 1].style.flexShrink = '1';

    document.body.style.userSelect = 'none';
    document.body.style.cursor     = 'col-resize';
  }, true);

  document.addEventListener('mousemove', function (e) {
    if (!drag) return;

    var dx   = e.clientX - drag.startX;
    var minPct = drag.totalPct * 0.1;
    var rawPct = (drag.startA + dx) / drag.rowW * 100;
    var pA   = Math.max(minPct, Math.min(drag.totalPct - minPct, rawPct));
    var pB   = drag.totalPct - pA; // exact complement — no independent rounding

    var ca = drag.cells[drag.idx];
    var cb = drag.cells[drag.idx + 1];
    ca.style.flexBasis = pA.toFixed(3) + '%'; ca.style.flexGrow = '0'; ca.style.flexShrink = '1';
    cb.style.flexBasis = pB.toFixed(3) + '%'; cb.style.flexGrow = '0'; cb.style.flexShrink = '1';

    // Reposition all handles in the row
    drag.row.querySelectorAll('[data-col-handle]').forEach(function (h) {
      var hi  = parseInt(h.getAttribute('data-col-handle'), 10);
      var rr  = drag.row.getBoundingClientRect();
      var cr  = drag.cells[hi].getBoundingClientRect();
      h.style.left = (cr.right - rr.left - 6) + 'px';
    });
  });

  function endDrag() {
    if (!drag) return;

    drag.pip.style.background = 'transparent';
    document.body.style.userSelect = '';
    document.body.style.cursor     = '';

    // Notify parent so the GrapesJS component model can persist the new styles
    var changes = Array.from(drag.cells)
      .filter(function (c) { return c.classList.contains('gjs-cell'); })
      .map(function (c) {
        return {
          gjsId: c.getAttribute('data-gjs-id'),
          style: {
            'flex-basis':  c.style.flexBasis  || null,
            'flex-grow':   c.style.flexGrow   || null,
            'flex-shrink': c.style.flexShrink || null,
          },
        };
      })
      .filter(function (c) { return c.gjsId; });

    if (changes.length) {
      window.parent.postMessage({ type: 'gjs-col-resize', changes: changes }, '*');
    }

    drag = null;
  }

  document.addEventListener('mouseup',  endDrag);
  // End drag if pointer leaves the iframe
  window.addEventListener('blur', endDrag);

  // Initialise existing rows and watch for new ones being added
  function initAll() {
    document.querySelectorAll('.gjs-row').forEach(setupRow);
  }

  new MutationObserver(initAll).observe(document.body, { childList: true, subtree: true });
  initAll();
})();
