/**
 * The border vocabulary the panel's composites are built on.
 *
 * These are CSS facts, not one editor's facts — a box has four sides and `border` is the shorthand
 * whatever is being authored. They lived in the email editor's `create.ts` because that is where the
 * first caller configured GrapesJS; here they belong to the shared layer, so a second editor
 * configuring its own StyleManager names the same properties without importing the first one's setup.
 *
 * The DEFAULTS are what a newly-set edge takes when the author gives only a width: a border with no
 * style draws nothing, so `solid` is the only useful answer, and a width is needed for the same
 * reason. Neither is a house style — they are the values that make the control's one input do
 * something.
 */

/** The shorthand property every side collapses into when the four agree. */
export const BORDER_ANCHOR = 'border';

/** Written when an edge is switched on with no width of its own. */
export const BORDER_WIDTH_DEFAULT = '1px';

/** Written when an edge is switched on with no style of its own; without it nothing is drawn. */
export const BORDER_STYLE_DEFAULT = 'solid';

/** Clockwise from the top, matching CSS shorthand order so a reader can check the two against
 *  each other. */
export const BORDER_SIDES = ['top', 'right', 'bottom', 'left'] as const;

export type BorderSide = (typeof BORDER_SIDES)[number];

/** The per-side composite's property name — `border-left`, not `border-left-width`: the composite
 *  owns all three longhands, and writing one directly puts a second declaration on the same edge. */
export function borderSideProperty(side: BorderSide): string {
	return `border-${side}`;
}
