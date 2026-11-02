// A shortcut to the rails, AT the thing: every item maps onto an action a rail already performs and keeps the rail's own words ("Move to top", not "Send to front"), because two names for one act is how an editor stops being learnable.
// Items are OMITTED, not disabled — deliberately the opposite of `position.ts`: a menu is built fresh and read top to bottom in one glance, so a greyed line is only a line to skip, and four dead rows on a single-block email is most of the menu.

import { canMove, type MoveDirection, type SelectionPosition } from './position.js';

export type CanvasMenuAction =
	| 'condition'
	| 'condition-clear'
	| 'move-top'
	| 'move-up'
	| 'move-down'
	| 'move-bottom'
	| 'duplicate'
	| 'delete'
	| 'page-style';

export interface CanvasMenuItem {
	action: CanvasMenuAction;
	label: string;
	/** Only ever a chord that really is bound — a menu teaching a shortcut that does nothing is worse than one teaching none. */
	shortcut?: string;
	variant?: 'destructive';
}

export interface CanvasMenuSubject {
	/** False means the PAGE, which is not selectable (`create.ts::protectPage`), so the menu offers the page's own controls rather than reading as an absence. */
	selected: boolean;
	/** Where it sits among its siblings, or null when it has no parent (the wrapper). */
	position: SelectionPosition | null;
	hasCondition: boolean;
	/** GrapesJS's own flags: a locked component (the preset locks some table plumbing) must not be offered a duplicate or delete that then silently refuses. */
	removable: boolean;
	copyable: boolean;
}

/** One group per separator; empty groups are dropped HERE, so the caller never reasons about a stray separator. */
export type CanvasMenuGroup = CanvasMenuItem[];

const MOVES: readonly (readonly [MoveDirection, CanvasMenuAction, string])[] = [
	['top', 'move-top', 'Move to top'],
	['up', 'move-up', 'Move up'],
	['down', 'move-down', 'Move down'],
	['bottom', 'move-bottom', 'Move to bottom']
];

// Read-only is the CALLER's decision, not a flag here: an `email_templates:read` principal gets no menu at all, so the browser's own survives and text can still be copied out.
export function canvasMenuGroups(subject: CanvasMenuSubject): CanvasMenuGroup[] {
	// The field submenu is prepended by the markup — its items are the template's own vocabulary — so
	// there is no insert group here.
	if (!subject.selected) return [[{ action: 'page-style', label: 'Page style' }]];

	const condition: CanvasMenuGroup = [
		{
			action: 'condition',
			// On a line you press, the label has to be a verb; "Visibility" is the right word for the panel's heading, not for this.
			label: subject.hasCondition ? 'Edit condition' : 'Add a condition'
		}
	];
	if (subject.hasCondition)
		condition.push({ action: 'condition-clear', label: 'Remove condition' });

	const moves: CanvasMenuGroup = MOVES.filter(([direction]) =>
		canMove(direction, subject.position)
	).map(([, action, label]) => ({ action, label }));

	const edit: CanvasMenuGroup = [];
	if (subject.copyable) edit.push({ action: 'duplicate', label: 'Duplicate' });
	if (subject.removable)
		edit.push({
			action: 'delete',
			label: 'Delete',
			// GrapesJS's own `core:component-delete` binding, which this editor leaves in place — a fact, not a promise the menu is making.
			shortcut: 'Del',
			variant: 'destructive'
		});

	return [condition, moves, edit].filter((group) => group.length > 0);
}
