/**
 * The live colour scheme, read off the one thing that actually decides it: the `.dark` class on
 * `<html>`.
 *
 * WHY THIS EXISTS. The only consumer that needed to *read* the scheme (the Sonner toaster, which
 * takes a `theme` prop) imported `mode` from `mode-watcher`. That was a second, disagreeing source
 * of truth: reading `mode.current` triggers mode-watcher's own `$derived` side effect, which writes
 * `document.documentElement.style.colorScheme` from ITS notion of the mode — a `mode-watcher-mode`
 * localStorage key we never write, so it falls through to `prefers-color-scheme`, i.e. the OS.
 * An inline style beats a stylesheet, so `theme.css`'s `.dark { color-scheme: dark }` lost and the
 * UA-rendered furniture — scrollbars, native form controls, the text caret, form autofill — stayed
 * light on a dark page whenever the user's chosen scheme differed from their OS. It also wrote the
 * class list, fighting the toggle.
 *
 * So: no second store. The class is written by exactly three things, all agreeing by construction
 * because they share `resolveScheme` — the SSR paint (`themeAttributes`), the blocking head script
 * (`themeHead`, which also owns the `prefers-color-scheme` listener) and `ThemeToggle`. Observing
 * the class therefore observes all three, and the UA scheme is left to `theme.css`, where
 * `color-scheme: light` on `:root` and `color-scheme: dark` on `.dark` follow the same class.
 *
 * `createSubscriber` means the `MutationObserver` is only attached while something is reactively
 * reading `.current`, and is torn down when it stops — and it is a no-op on the server, where the
 * getter short-circuits to the SSR default.
 */
import { createSubscriber } from 'svelte/reactivity';
import { DARK_CLASS, type ColourScheme } from '@graphene/auth';

/**
 * What SSR reports. The server DOES know the scheme (it renders the class), but it knows it in the
 * `handle` hook, not here, and no consumer needs it before hydration — a toast cannot exist in the
 * first paint. Hydration corrects it on mount.
 */
const SSR_SCHEME: ColourScheme = 'light';

const subscribe = createSubscriber((update) => {
	const observer = new MutationObserver(update);
	observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
	return () => observer.disconnect();
});

/** `colourScheme.current` — `'light' | 'dark'`, reactive to the class flipping. */
export const colourScheme = {
	get current(): ColourScheme {
		if (typeof document === 'undefined') return SSR_SCHEME;
		subscribe();
		return document.documentElement.classList.contains(DARK_CLASS) ? 'dark' : 'light';
	}
};
