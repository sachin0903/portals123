/**
 * The client portal's header controls — search, the bell, the message button, the way back —
 * all wear the same liquid-glass round, the one the floating tab bar wears (`client-shell`).
 * One string, so a new control cannot arrive in the staff tools' flat ghost treatment next to
 * three glass ones. The icon size pairs with it: a touch target on a phone, a hair smaller on
 * a desktop bar.
 */
export const CLIENT_HEADER_BUTTON_CLASS =
	'glass text-foreground/70 hover:text-foreground focus-visible:ring-ring tap-target relative flex size-10 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:outline-none md:size-9';

export const CLIENT_HEADER_ICON_CLASS = 'size-5 md:size-4';

/** The same round, lit, for the control whose page is the one open (the tab bar's active pill). */
export const CLIENT_HEADER_BUTTON_ACTIVE_CLASS =
	'bg-primary/12 text-primary ring-primary/20 shadow-[inset_0_1px_0_0_var(--glass-highlight)] ring-1 ring-inset hover:text-primary';
