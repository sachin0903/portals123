import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { canMove, moveTargetIndex, type MoveDirection } from './position.js';

// Reordering is `sel.move(parent, { at })` on a live GrapesJS instance, which needs `window` and can't run here — hence the pure-function arithmetic below; the shipped bug was a button that ran, reported nothing, and moved nothing.
const editor = readFileSync(join(import.meta.dirname, 'email-editor.svelte'), 'utf8');

const pos = (index: number, count: number, movable?: boolean) => ({ index, count, movable });
const ALL: MoveDirection[] = ['up', 'down', 'top', 'bottom'];

describe('canMove', () => {
	it('offers up/top only off the top, and down/bottom only off the bottom', () => {
		for (const d of ALL) expect(canMove(d, pos(1, 3))).toBe(true);
		for (const d of ['up', 'top'] as const) {
			expect(canMove(d, pos(0, 3))).toBe(false);
			expect(canMove(d, pos(2, 3))).toBe(true);
		}
		for (const d of ['down', 'bottom'] as const) {
			expect(canMove(d, pos(0, 3))).toBe(true);
			expect(canMove(d, pos(2, 3))).toBe(false);
		}
	});

	it('offers nothing for a lone child, an immovable component, no selection, or a stale index', () => {
		for (const d of ALL) {
			expect(canMove(d, pos(0, 1))).toBe(false);
			expect(canMove(d, pos(1, 3, false))).toBe(false);
			expect(canMove(d, null)).toBe(false);
			// State can be read mid-move (`move` removes before re-appending), so a stale index must not produce an `at` computed from it.
			expect(canMove(d, pos(-1, 3))).toBe(false);
			expect(canMove(d, pos(3, 3))).toBe(false);
		}
		expect(canMove('up', pos(1, 3, undefined))).toBe(true);
	});
});

describe('moveTargetIndex', () => {
	it('moves DOWN to index + 2 — never index + 1, which GrapesJS ignores', () => {
		// `Component.move` treats `at === index + 1` as already there (`sameIndex`, grapes.mjs :30336) and returns without moving anything — this is the assertion that keeps the button working.
		expect(moveTargetIndex('down', pos(0, 3))).toBe(2);
		expect(moveTargetIndex('down', pos(1, 3))).toBe(3);
		expect(moveTargetIndex('down', pos(3, 5))).not.toBe(4);
		// `up` is the plain index above; `bottom` is `count`, not `count - 1`, because `move` removes the component before re-appending and then subtracts one itself for a same-parent destination.
		expect(moveTargetIndex('up', pos(3, 5))).toBe(2);
		expect(moveTargetIndex('up', pos(1, 2))).toBe(0);
		expect(moveTargetIndex('top', pos(3, 5))).toBe(0);
		expect(moveTargetIndex('bottom', pos(3, 5))).toBe(5);
	});

	it('returns null rather than an index for an unavailable move', () => {
		// So an unavailable direction cannot become a call that does nothing but looks like it worked.
		expect(moveTargetIndex('up', pos(0, 3))).toBeNull();
		expect(moveTargetIndex('down', pos(2, 3))).toBeNull();
		expect(moveTargetIndex('top', null)).toBeNull();
		expect(moveTargetIndex('bottom', pos(0, 1))).toBeNull();
	});
});

// GrapesJS's own reordering algorithm, transcribed from `Component.prototype.move` (grapes.mjs :30330-30347) for the same-parent case — re-read that method before trusting this on a grapesjs upgrade.
function vendorMove<T>(children: readonly T[], index: number, at: number): T[] {
	if (index === at || index === at - 1) return [...children]; // no-op: "already there"
	const target = at > index ? at - 1 : at;
	const next = [...children];
	const [moved] = next.splice(index, 1);
	next.splice(target, 0, moved);
	return next;
}

describe('the order GrapesJS actually ends up with', () => {
	const BLOCKS = ['header', 'hero', 'body', 'footer'] as const;
	const move = (index: number, direction: MoveDirection) => {
		const at = moveTargetIndex(direction, pos(index, BLOCKS.length));
		expect(at, `${direction} from ${index} must be available`).not.toBeNull();
		return vendorMove(BLOCKS, index, at as number);
	};

	it('moves a block exactly one place, or to either end', () => {
		expect(move(1, 'down')).toEqual(['header', 'body', 'hero', 'footer']);
		expect(move(0, 'down')).toEqual(['hero', 'header', 'body', 'footer']);
		expect(move(2, 'up')).toEqual(['header', 'body', 'hero', 'footer']);
		expect(move(2, 'top')).toEqual(['body', 'header', 'hero', 'footer']);
		expect(move(1, 'bottom')).toEqual(['header', 'body', 'footer', 'hero']);
	});

	it('would move NOTHING at index + 1 — the bug this arithmetic exists to avoid', () => {
		expect(vendorMove(BLOCKS, 1, 2)).toEqual([...BLOCKS]);
	});
});

describe('the rail wires them up', () => {
	it('disables each direction from `canMove` rather than hiding the control', () => {
		for (const d of ALL) {
			expect(editor).toMatch(new RegExp(`disabled=\\{!canMove\\('${d}', position\\)\\}`));
			expect(editor).toMatch(new RegExp(`onclick=\\{\\(\\) => moveSelection\\('${d}'\\)\\}`));
		}
		expect(editor).toMatch(/\{#if !readOnly && position\}/);
	});

	it('moves in ONE synchronous tick, so it is ONE undo step', () => {
		// backbone-undo fuses everything raised in one callstack under `magicFusionIndex`, so an `await` here would split the move into two undo steps; the selection must also survive, since `move` drops the moved component from it.
		const body = editor.match(/function moveSelection\([\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(body).toContain('sel.move(parent, { at })');
		expect(body).not.toMatch(/\bawait\b/);
		expect(editor).not.toMatch(/async function moveSelection/);
		expect(body).toContain('editor.select?.(sel)');
	});
});
