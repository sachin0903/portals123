// The arithmetic lives here because one line of it is a trap that reads as working code: `Component.move(parent, { at })` short-circuits on `sameIndex = index === at || index === at - 1` (grapes.mjs :30334-30337), so moving DOWN one place cannot be `at: index + 1` — that satisfies `index === at - 1` and the call silently does nothing. It must be `index + 2`, and `move` then subtracts one itself for the same-parent case. A button that does nothing at all is invisible to a test asserting only that the call was made, so the indices are pure, with a case per direction.

export type MoveDirection = 'up' | 'down' | 'top' | 'bottom';

export interface SelectionPosition {
	index: number;
	count: number;
	/** GrapesJS's `draggable: false` (the preset locks some table plumbing). Absent counts as movable, so a caller that cannot read the flag gets the ordinary behaviour rather than four dead buttons. */
	movable?: boolean;
}

// DISABLED on a false, never hidden: buttons that appear and vanish as the selection moves through a list are harder to use than greyed ones, and the author needs to see that "move up" exists while looking at the top block.
export function canMove(direction: MoveDirection, pos: SelectionPosition | null): boolean {
	if (!pos) return false;
	if (pos.movable === false) return false;
	// A lone child has nowhere to go, and a nonsensical index (a stale read between a remove and `move`'s re-append) must not offer a move either.
	if (pos.count < 2 || pos.index < 0 || pos.index >= pos.count) return false;
	return direction === 'up' || direction === 'top' ? pos.index > 0 : pos.index < pos.count - 1;
}

// Null when the move is unavailable, so a caller cannot turn an unavailable direction into a no-op that looks like it worked. `down` is `index + 2` and `bottom` is `count` for the short-circuit reason above: `move` removes before it re-appends.
export function moveTargetIndex(
	direction: MoveDirection,
	pos: SelectionPosition | null
): number | null {
	if (!canMove(direction, pos) || !pos) return null;
	switch (direction) {
		case 'top':
			return 0;
		case 'up':
			return pos.index - 1;
		case 'down':
			return pos.index + 2;
		case 'bottom':
			return pos.count;
	}
}
