// A field's look lives once here; a control composes it and overrides only the one property it
// genuinely differs in (`MultiSelect` passes `h-auto min-h-8` for its badges). `text-base
// md:text-sm` must survive: iOS Safari zooms the page for input text under 16px.

/** The inspector tone — filled in a rail, outlined on a form — opted into by an ancestor's
 *  `data-field-tone`, not a prop, so anything composing the shell picks it up. Keep `in-data-*`: it
 *  compiles to `:where([attr]) .utility`, no specificity, so a caller's own `class` override still
 *  wins, where `[[data-field-tone=filled]_&]:` is two classes' worth and would out-rank it. */
export const FIELD_TONE_FILLED =
	'in-data-[field-tone=filled]:bg-muted in-data-[field-tone=filled]:border-border/50 in-data-[field-tone=filled]:rounded-xl dark:in-data-[field-tone=filled]:bg-input/50';

/** Border, background, size, radius, type. What makes the box a box. */
export const FIELD_SHELL = `border-input dark:bg-input/30 h-8 w-full min-w-0 rounded-lg border bg-transparent px-2.5 py-1 text-base transition-colors outline-none md:text-sm ${FIELD_TONE_FILLED}`;

/** The focus ring for a control that IS the focusable element (a real `<input>`, a trigger). */
export const FIELD_FOCUS =
	'focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-3';

/** For a wrapper: `MoneyInput` holds the symbol inside the box, so the ring is the container's. */
export const FIELD_FOCUS_WITHIN =
	'focus-within:border-ring focus-within:ring-ring/50 focus-within:ring-3';

/** Driven by `aria-invalid`, so a control sets one attribute rather than juggling classes. */
export const FIELD_INVALID =
	'aria-invalid:border-destructive dark:aria-invalid:border-destructive/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:ring-3';

/** For a control whose disabled state is the `:disabled` pseudo-class (input, button). */
export const FIELD_DISABLED =
	'disabled:bg-input/50 dark:disabled:bg-input/80 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50';

/** The whole shell for the common case: a focusable control that can be invalid or disabled. */
export const FIELD_CLASS = `${FIELD_SHELL} ${FIELD_FOCUS} ${FIELD_INVALID} ${FIELD_DISABLED}`;

/** For a wrapper: a div has no `:disabled`/`aria-invalid`, so `MoneyInput` styles those itself. */
export const FIELD_WRAPPER_CLASS = `${FIELD_SHELL} ${FIELD_FOCUS_WITHIN}`;
