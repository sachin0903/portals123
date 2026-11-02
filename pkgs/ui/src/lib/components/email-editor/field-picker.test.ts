// This picker lives in a static asset run inside the GrapesJS iframe (`apps/{platform,admin}/static/gjs-email-jinja-hl.js`), so nothing here can import or execute it — these tests pin behaviour by reading the source directly.
// Vocabulary is REQUESTED, not pushed: GrapesJS evaluates `canvas.scripts` after its own init, so a push from `onMount` would arrive before any listener exists and be silently dropped — the picker just never opens.
// The menu is excluded from the highlighter's token-painting pass, or its own chips would be painted over the menu's own rows.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles into `C:\C:\…` (the trap static-assets.test.ts records making a whole suite vacuous).
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const read = (p: string): string => readFileSync(join(REPO_ROOT, p), 'utf8');

const SCRIPT = read('apps/platform/static/gjs-email-jinja-hl.js');
const ADMIN_SCRIPT = read('apps/admin/static/gjs-email-jinja-hl.js');
const EDITOR = read('pkgs/ui/src/lib/components/email-editor/email-editor.svelte');

const PICKER = SCRIPT.slice(SCRIPT.indexOf('THE `{{` PICKER'));

describe('the canvas script is one file, served twice', () => {
	// Each app serves its own copy; they've drifted before under identical names (docs/design-language.md), so equality is the only useful assertion.
	it('is byte-identical in both apps', () => {
		expect(ADMIN_SCRIPT).toBe(SCRIPT);
	});

	it('carries the picker at all', () => {
		expect(PICKER).not.toBe('');
		expect(PICKER).toContain('function initPicker');
	});
});

describe('the vocabulary is asked for, not waited for', () => {
	it('the frame requests it as soon as the picker initialises', () => {
		const init = PICKER.slice(PICKER.indexOf('function initPicker'));
		expect(init).toContain("type: 'gjs-email-fields-request'");
		expect(init).toContain('window.parent.postMessage');
	});

	it('the parent answers that request', () => {
		expect(EDITOR).toContain("'gjs-email-fields-request'");
		expect(EDITOR).toContain('publishPickerFields');
	});

	it('the frame accepts the answer', () => {
		expect(SCRIPT).toContain("e.data.type === 'gjs-email-fields'");
	});

	it('stays inert until it has one — an empty menu is worse than none', () => {
		expect(PICKER).toMatch(
			/function openMenu\(\)\s*\{\s*\n?\s*if \(!fieldGroups\.length\) return;/
		);
	});
});

describe('a pick must not end the editing session it happens in', () => {
	const pickOn = PICKER.slice(
		PICKER.indexOf('function pickOn'),
		PICKER.indexOf('function hoverOn')
	);

	it('keeps the focus and keeps the RTE', () => {
		// preventDefault keeps the focus and stopPropagation keeps the RTE — without both the field lands but the next keystroke goes nowhere.
		expect(pickOn).toContain('ev.preventDefault()');
		expect(pickOn).toContain('ev.stopPropagation()');
	});

	it('picks on mousedown, because a click arrives after the caret has gone', () => {
		expect(PICKER).toContain("addEventListener('mousedown', pickOn(index))");
		expect(PICKER).not.toContain("addEventListener('click', pickOn(index))");
	});
});

describe('the field markup belongs to the parent', () => {
	it('the script writes no field markup of its own', () => {
		// Asserts on the attributes, not a substring: a literal copy here would drift from `fieldSpanHtml`, and a substring match would false-fail since the message names (`gjs-email-fields`, `gjs-email-field-inserted`) also contain "email-field".
		expect(PICKER).not.toContain('data-gjs-type="email-field"');
		expect(PICKER).not.toContain('data-field=');
		expect(PICKER).not.toContain('<span');
	});

	it('it inserts the html it was handed', () => {
		expect(PICKER).toContain('field.html');
		expect(PICKER).toContain("document.execCommand('insertHTML'");
	});

	it('the parent hands over the SAME markup every other path uses', () => {
		const publish = EDITOR.slice(
			EDITOR.indexOf('function publishPickerFields'),
			EDITOR.indexOf('onMount(() => {')
		);
		expect(publish).toContain('html: fieldMarkup(field.token)');
	});

	it('offers nothing at all when the editor is read-only', () => {
		const publish = EDITOR.slice(
			EDITOR.indexOf('function publishPickerFields'),
			EDITOR.indexOf('onMount(() => {')
		);
		expect(publish).toMatch(/readOnly\s*\n?\s*\?\s*\[\]/);
	});
});

describe('the menu is chrome, and the rest of the canvas machinery must know it', () => {
	it('is excluded from the highlighter, whose chips would otherwise land on it', () => {
		expect(SCRIPT).toMatch(/SKIP_SELECTOR\s*=[\s\S]{0,200}MENU_ID/);
	});

	it('lives in the body, not in the content — nothing it draws can reach getHtml()', () => {
		expect(PICKER).toContain('document.body.appendChild(menuEl)');
	});

	it('closes the question on Escape, an outside press and the RTE letting go', () => {
		expect(PICKER).toContain("e.key === 'Escape'");
		expect(PICKER).toContain("document.addEventListener('focusout', closeMenu, true)");
	});
});

describe('the trigger is only a trigger where one makes sense', () => {
	const readTrigger = PICKER.slice(
		PICKER.indexOf('function readTrigger'),
		PICKER.indexOf('function styleMenu')
	);

	it('requires a collapsed caret in a text node the RTE is editing', () => {
		expect(readTrigger).toContain('sel.isCollapsed');
		expect(readTrigger).toContain('Node.TEXT_NODE');
		expect(readTrigger).toContain('[contenteditable="true"]');
	});

	it('gives up once the token is closed, so a finished `{{ x }}` reopens nothing', () => {
		expect(readTrigger).toContain("inner.indexOf('}') !== -1");
	});

	it('takes the NEAREST opener, so `{{ a {{ b` completes the second', () => {
		expect(readTrigger).toContain("before.lastIndexOf('{{')");
	});
});

describe('the parent hears about an insert', () => {
	it('syncs immediately rather than waiting for the RTE to commit on blur', () => {
		// GrapesJS commits contenteditable content on `rte:disable`; syncing immediately (not waiting for blur) covers an author who types a field then saves right away.
		expect(PICKER).toContain("type: 'gjs-email-field-inserted'");
		const handler = EDITOR.slice(
			EDITOR.indexOf('const onFrameMessage'),
			EDITOR.indexOf("window.addEventListener('message', onFrameMessage)")
		);
		expect(handler).toContain("'gjs-email-field-inserted'");
		expect(handler).toContain('emitNow()');
	});

	it('and stops listening when the editor goes away', () => {
		expect(EDITOR).toContain("window.removeEventListener('message', onFrameMessage)");
	});
});

describe('a rail click lands at the caret', () => {
	// Replaces the rail's old end-of-block append — which needed a field found and dragged back mid-sentence — with an insert at the caret via the same path the `{{` picker uses.

	it('asks the FRAME to insert, because only the frame holds the caret', () => {
		// The caret is a selection inside the iframe's document; the parent can't reach it, only decide which path — hence the message rather than a direct call.
		expect(EDITOR).toContain("type: 'gjs-email-insert-field'");
		expect(SCRIPT).toContain("e.data.type === 'gjs-email-insert-field'");
	});

	it('tries the caret ALWAYS, and lets the frame decide', () => {
		// It used to gate on `isEditingText()` — GrapesJS's own editing flag — and that reads false by
		// the time this runs: clicking the rail's field row moves focus out of the canvas and GrapesJS
		// ends the RTE session on the way out. Every insert then appended a block at the END of the
		// email, the opposite of the flyout's own promise. The frame is the only side that can see the
		// caret, so it answers and its refusal is what falls back.
		// Scoped to the function, not the file: the comment there NAMES the removed helper, the same
		// false positive `switch-columns.test.ts` records hitting.
		const fn = EDITOR.split('function insertToken')[1]?.split('\n\t}')[0] ?? '';
		expect(fn).toMatch(/const frame = frameWindow\(\)/);
		expect(fn).toMatch(/pendingInsert = markup/);
	});

	it('appends only on the frame’s refusal, and appends a real COMPONENT', () => {
		// `ok: false` is "there was no caret in an editable element". And the fallback PARSES the markup
		// instead of handing it over as a `content` string: a text component keeps `content` as raw
		// inner HTML, so the chip never became a component, could not be selected, and the whole Field
		// panel — its filter and its "If empty" — was unreachable for any field placed this way.
		expect(EDITOR).toMatch(/if \(reply.ok === false && markup\) return appendField/);
		const fn = EDITOR.split('function appendField')[1]?.split('\n\t}')[0] ?? '';
		expect(fn).toContain('pageCell(editor)');
		expect(fn).toMatch(/target\?\.append\?\.\(`<div style="padding:10px">\$\{markup\}<\/div>`\)/);
		// Selected on the CHIP where there is one, so the Field panel opens on the field itself.
		expect(fn).toMatch(/added\?\.find\?\.\('\[data-field\]'\)/);
	});

	it('refuses when there is no LIVE caret, and names the component instead', () => {
		// A rail click ends the RTE session on its way out of the canvas, and ending it re-parses the
		// element — so a saved Range is detached and `isConnected` is the test that matters. The refusal
		// carries the component the author was last typing in, because the parent cannot see in here.
		// Sliced to the next declaration rather than on a brace, so the assertion cannot
		// wander into a neighbouring function.
		const fn = SCRIPT.split('function insertAtCaret')[1]?.split('function ')[0] ?? '';
		expect(fn).toMatch(/liveEditable\(\)/);
		expect(fn).toMatch(/return refuse\(token\)/);
		expect(SCRIPT).toMatch(/function liveEditable\(\)/);
		expect(SCRIPT).toContain('[contenteditable="true"]');
		expect(SCRIPT).toMatch(/node\.isConnected/);
		expect(SCRIPT).toMatch(/componentId: lastEditableId/);
	});

	it('remembers the component ID, not the element, because the re-parse replaces it', () => {
		// GrapesJS sets an element's `id` to its component's id, and the re-parse mints a new element
		// carrying the same id — so a held element reference goes stale where the id does not.
		expect(SCRIPT).toMatch(/var lastEditableId = null/);
		expect(SCRIPT).toMatch(/addEventListener\('selectionchange'/);
		expect(SCRIPT).toMatch(/lastEditableId = editable\.id/);
	});

	it('focuses the editable before inserting', () => {
		// `execCommand` acts on the FOCUSED document, and a rail click has moved focus out of the frame.
		// Sliced to the next declaration rather than on a brace, so the assertion cannot
		// wander into a neighbouring function.
		const fn = SCRIPT.split('function insertAtCaret')[1]?.split('function ')[0] ?? '';
		expect(fn).toMatch(/editable\.focus\(\)/);
	});
});

describe('the two insert paths agree, and the menu is navigable', () => {
	it('adds no trailing space, so the saved html matches the picker’s', () => {
		// `pick()` removed its own for the same reason ("cost a visible gap"). This path writes into
		// content `editor.getHtml()` SERIALISES, so an `&nbsp;` here is a gap in the SENT email — and
		// the two ways of doing one thing produced different saved HTML.
		// Sliced to the next declaration rather than on a brace, so the assertion cannot wander into a
		// neighbouring function — and stripped of comments, because the note explaining the removal
		// NAMES the entity it removed (the same false positive `switch-columns.test.ts` records).
		const fn = (SCRIPT.split('function insertAtCaret')[1]?.split('function ')[0] ?? '')
			.split('\n')
			.filter((l) => !l.trim().startsWith('//'))
			.join('\n');
		expect(fn).toContain("insertHTML', false, html)");
		expect(fn).not.toContain('&nbsp;');
	});

	it('scrolls the active row into the menu’s own scroller', () => {
		// The panel shows ~10 rows of a ~17-field vocabulary, so without this the highlight walked off
		// the bottom: the list did not move, no row looked selected, and Enter inserted a field the
		// author never saw. `nearest` so an already-visible row does not jog the list.
		const fn = SCRIPT.split('function paintActive')[1]?.split('function ')[0] ?? '';
		expect(fn).toContain('scrollIntoView');
		expect(fn).toContain("block: 'nearest'");
	});
});
