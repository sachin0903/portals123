import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Asserted on the SOURCE (no jsdom/happy-dom; needs a real GrapesJS-hosted browser, see `rail.test.ts`) — read-only default, click-proof unlock, accurate warning, apply that no-ops unchanged.
const source = readFileSync(join(import.meta.dirname, 'code-view.svelte'), 'utf8');

/** Comments stripped, for asserting a string is ABSENT — a rule that names what it forbids would otherwise contain the very string a raw scan checks for (`rail.test.ts` records the same trap). */
const stripComments = (text: string): string =>
	text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '');

describe('read-only is the default state', () => {
	it('starts locked', () => {
		expect(source).toContain('let unlocked = $state(false)');
	});

	it('renders the editing overlay ONLY while unlocked', () => {
		// The textarea is the only route to the draft; matched structurally (not a length-bounded regex, which a comment above it would push out of a fixed window).
		const overlay = source.indexOf('<Textarea');
		const guard = source.lastIndexOf('{#if unlocked}', overlay);
		expect(overlay).toBeGreaterThan(-1);
		expect(guard).toBeGreaterThan(-1);
		expect(source.slice(guard, overlay)).not.toContain('{/if}');
		expect(source.match(/<Textarea/g) ?? []).toHaveLength(1);
	});

	it('offers copy through the shared component, not a hand-rolled clipboard call', () => {
		// CopyButton owns not-throwing on a rejected write and naming what's copied — stops a fifth hand-rolled clipboard call.
		expect(source).toContain('<CopyButton');
		expect(source).not.toContain('navigator.clipboard');
	});

	it('never injects markup', () => {
		// Why `highlight.ts` returns segments rather than a markup string — its header has the argument.
		expect(source).not.toContain('{@html');
	});
});

describe('the unlock is genuinely gated', () => {
	it('cannot be reached by a click', () => {
		expect(source).toMatch(/onclick=\{\(\) => \(confirmOpen = !confirmOpen\)\}/);
		expect(source).not.toMatch(/onclick=\{unlock\}/);
		expect(source).not.toMatch(/onclick=\{\(\) => unlock\(\)\}/);
	});

	it('is reached only from the hold timer', () => {
		// One call site, the hold-timer interval — matched on the CALL, not the declaration (which also contains "unlock()").
		expect(source.match(/\bunlock\(\);/g) ?? []).toHaveLength(1);
		const start = /function startHold\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		expect(start, 'startHold not found').not.toBe('');
		expect(start).toMatch(/if \(holdTick >= HOLD_MS\) \{\s*cancelHold\(\);\s*unlock\(\);/);
	});

	it('cancels on every way a hold can end', () => {
		for (const handler of [
			'onpointerup={cancelHold}',
			'onpointerleave={cancelHold}',
			'onpointercancel={cancelHold}',
			'onkeyup={cancelHold}',
			'onblur={cancelHold}'
		])
			expect(source).toContain(handler);
		// Timer cleared on teardown too, or a pane closed mid-hold unlocks a pane that's gone.
		expect(source).toMatch(/\$effect\(\(\) => \(\) => cancelHold\(\)\)/);
	});

	it('can be held from the keyboard, and a key repeat does not restart it', () => {
		const key =
			/function onHoldKey\(event: KeyboardEvent\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		expect(key, 'onHoldKey not found').not.toBe('');
		expect(key).toMatch(/event\.key !== ' ' && event\.key !== 'Enter'/);
		expect(key).toContain('if (!event.repeat) startHold()');
	});

	it('is an inline panel, not a Popover', () => {
		// `Popover.Content` portals to `document.body` and misses clicks inside a focus-trapped surface (the standing `pkgs/ui` gotcha); matched on the IMPORT/ELEMENT, not the word, since the rule-quoting comment above the confirm would false-positive a word scan.
		expect(source).not.toMatch(/from '[^']*popover/);
		expect(source).not.toContain('<Popover');
	});
});

describe('the warning says what is true of THIS editor', () => {
	// The panel's own markup, opening tag to action row — never split on `</div>`, which stops at the first nested element and would make three assertions below vacuous rather than failing.
	const confirm =
		/class="code-confirm"[\s\S]*?class="code-confirm-actions"/.exec(source)?.[0] ?? '';

	it('was found at all', () => {
		// Guards the extraction above — an empty string would satisfy a `not.toContain` by accident.
		expect(confirm).not.toBe('');
	});

	it('names the attributes the panel depends on', () => {
		for (const attr of ['data-if', 'data-choice', 'data-repeat', 'data-fallback', 'data-field'])
			expect(confirm).toContain(`<code>${attr}</code>`);
		expect(confirm).toContain('stops being editable in the panel');
	});

	it('says the compile step is one-way', () => {
		// `conditions/compile.ts` is one-way: hand-written Jinja here is permanently invisible to the panel.
		expect(confirm).toMatch(/Jinja you write here stays Jinja/);
		expect(confirm).toContain('read it back into a condition');
	});

	it('says it cannot be undone, which is a promise the apply keeps', () => {
		// A real limitation: apply runs inside `seed()` with undo paused (see `applyCode`, email-editor.svelte).
		expect(confirm).toContain('cannot be undone');
	});
});

describe('what may be edited, and when', () => {
	it('offers no unlock without an apply handler, and never for the sent shape', () => {
		expect(source).toContain("const canEdit = $derived(!!onapply && shape === 'source')");
		expect(source).toMatch(/\{:else if canEdit\}/);
		expect(source).toMatch(/\{#if confirmOpen && canEdit\}/);
	});

	it('does not touch the editor when the text has not changed', () => {
		// draftBase: an identical apply still re-parses and moves content, reading as unsaved for no reason.
		const apply = /function apply\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		expect(apply, 'apply not found').not.toBe('');
		expect(apply).toContain('if (draft !== draftBase) onapply?.(draft)');
	});

	it('wraps the overlay exactly as the highlight layer wraps', () => {
		// The fragile half of soft wrapping: the overlay textarea and the highlighted lines must wrap at the same width or the caret drifts off its character — same `pre-wrap`, shared line height, no `wrap="off"` (would override the CSS).
		expect(source).toContain('whitespace-pre-wrap');
		expect(source).toContain('leading-[var(--code-lh)]');
		// Comments stripped: the overlay's own note has to NAME `wrap="off"` to explain its absence, so a raw scan would otherwise assert the absence of a string the prose requires (the `rail.test.ts` false positive).
		expect(stripComments(source)).not.toContain('wrap="off"');
		expect(source).toContain('left-[var(--code-gutter-w)]');
		expect(source).toContain('right-3');
	});

	it('changes no text to wrap it', () => {
		// VS Code's Alt+Z, not a reformat: byte-identical wrap on or off, so apply is unaffected — CSS-only, no width notion, no line-splitting.
		expect(source).not.toMatch(/\.slice\(0,\s*\d{2,}\)/);
		expect(source).not.toContain('wordWrap');
		expect(source).not.toContain('maxLineLength');
	});

	it('folds nothing while editing', () => {
		expect(source).toContain('const ranges = $derived(unlocked ? [] : foldRanges(lines))');
	});

	it('drops stale folds when the document is renumbered', () => {
		// The pane is LIVE and a fold is a line-number pair, so an editor edit leaves a stale fold hiding the wrong region.
		expect(source).toMatch(/if \(n === foldedAt\) return;[\s\S]{0,120}collapsedList = \[\]/);
	});
});

describe('the two shapes', () => {
	it('says which one is on screen, and why the difference matters', () => {
		expect(source).toContain('What the visual editor holds');
		expect(source).toContain('What a recipient receives');
		expect(source).toContain('label="Which HTML"');
	});

	it('says which one can be EDITED, on the tab itself', () => {
		expect(source).toContain('the only shape a hand edit can be applied to');
		expect(source).toContain('switch to Editor for the shape an edit can be written back to');
	});

	it('lists Sent first, because it is the default', () => {
		// A control whose first-shown segment is the second one reads as a choice already changed.
		expect(source).toMatch(
			/\{ value: 'sent', label: 'Sent' \},\s*\{ value: 'source', label: 'Editor' \}/
		);
	});
});
