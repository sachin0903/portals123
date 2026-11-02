import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { canvasMenuGroups, type CanvasMenuSubject } from './canvas-menu';

// Pinned as a pure function, not by rendering — what breaks here is a menu offering an action the editor then refuses (Duplicate on a locked row, Move up on the first block), invisible in a screenshot of a menu that looks fine.

const subject = (over: Partial<CanvasMenuSubject> = {}): CanvasMenuSubject => ({
	selected: true,
	position: { index: 1, count: 3, movable: true },
	hasCondition: false,
	removable: true,
	copyable: true,
	...over
});

const actions = (over: Partial<CanvasMenuSubject> = {}) =>
	canvasMenuGroups(subject(over))
		.flat()
		.map((item) => item.action);

describe('canvasMenuGroups', () => {
	it('offers the page its own controls when nothing is selected', () => {
		// Nothing selected IS the page's own state (`create.ts::protectPage` makes it deliberately unselectable), so the menu has to agree with the rail's idle panel.
		const groups = canvasMenuGroups(subject({ selected: false }));
		expect(groups.flat().map((i) => i.action)).toEqual(['page-style']);
		expect(groups).toHaveLength(1);
	});

	it('omits a move it cannot make, rather than greying it', () => {
		// Deliberately unlike the rail (whose buttons disable at the ends to stay learnable) — a menu built fresh on every open has nothing to teach by showing a dead row, so it omits the move instead.
		expect(actions({ position: { index: 0, count: 3, movable: true } })).not.toContain('move-up');
		expect(actions({ position: { index: 0, count: 3, movable: true } })).toContain('move-down');
		expect(actions({ position: { index: 2, count: 3, movable: true } })).not.toContain('move-down');
		for (const position of [
			{ index: 0, count: 1, movable: true },
			{ index: 1, count: 3, movable: false }
		]) {
			const offered = actions({ position });
			expect(offered.filter((a) => a.startsWith('move-'))).toEqual([]);
		}
		expect(
			canvasMenuGroups(subject({ position: null }))
				.flat()
				.map((i) => i.action)
		).not.toContain('move-up');
	});

	it('says which of the two things the condition item will do', () => {
		const label = (hasCondition: boolean) =>
			canvasMenuGroups(subject({ hasCondition }))
				.flat()
				.find((i) => i.action === 'condition')?.label;
		expect(label(false)).toBe('Add a condition');
		expect(label(true)).toBe('Edit condition');
		expect(actions({ hasCondition: false })).not.toContain('condition-clear');
		expect(actions({ hasCondition: true })).toContain('condition-clear');
	});

	it("honours GrapesJS's own locks rather than trusting the refusal", () => {
		expect(actions({ copyable: false })).not.toContain('duplicate');
		expect(actions({ removable: false })).not.toContain('delete');
		expect(actions({ copyable: false, removable: false })).toEqual([
			'condition',
			'move-top',
			'move-up',
			'move-down',
			'move-bottom'
		]);
	});

	it('marks Delete destructive and names the chord that already does it', () => {
		const remove = canvasMenuGroups(subject())
			.flat()
			.find((i) => i.action === 'delete');
		expect(remove?.variant).toBe('destructive');
		// GrapesJS binds `backspace, delete` to `core:component-delete` itself (`SUPERSEDED_KEYMAPS` drops only undo/redo), so the shortcut hint is a fact, not a promise.
		expect(remove?.shortcut).toBe('Del');
	});

	it('never emits an empty group, so a separator cannot land on nothing', () => {
		for (const over of [
			{},
			{ selected: false },
			{ copyable: false, removable: false },
			{ position: null as CanvasMenuSubject['position'] },
			{ hasCondition: true, position: { index: 0, count: 1 } }
		])
			for (const group of canvasMenuGroups(subject(over))) expect(group.length).toBeGreaterThan(0);
	});
});

describe('the menu is a shortcut to the rail, not a second vocabulary', () => {
	const menu = readFileSync(join(import.meta.dirname, 'canvas-menu.svelte'), 'utf8');
	const editor = readFileSync(join(import.meta.dirname, 'email-editor.svelte'), 'utf8');

	it('uses the rail header’s own words for the four moves', () => {
		// Matches the rail's own button titles verbatim — two names for one act is how an editor stops being learnable.
		const labels = canvasMenuGroups(subject())
			.flat()
			.map((i) => i.label);
		expect(labels).toContain('Move to top');
		expect(labels).toContain('Move up');
		expect(labels).toContain('Move down');
		expect(labels).toContain('Move to bottom');
	});

	it('routes every action through the editor, and performs none itself', () => {
		// The component is pure presentation: it emits an action and the editor decides, which is what keeps the menu and the rail from drifting in what they DO.
		expect(menu).not.toMatch(/editor\.|getSelected|emitNow/);
		expect(editor).toMatch(/function runMenuAction\(action: CanvasMenuAction\)/);
		for (const action of canvasMenuGroups(subject())
			.flat()
			.map((i) => i.action))
			expect(editor, `${action} must be handled`).toContain(`case '${action}':`);
	});

	it('is not offered at all in read-only mode', () => {
		// A template an author may not edit still wants the browser's own context menu (copying text out of it is real), so the listener returns early rather than rendering a menu of dead rows.
		expect(editor).toMatch(/function openCanvasMenu[\s\S]{0,600}?if \(readOnly/);
		expect(editor).toMatch(/\{#if !readOnly\}\s*<CanvasMenu/);
	});
});
