/** Where "Back" actually goes for the page on screen — published by the app, read by the shell.
 *
 * A back link used to be a literal prop at each call site: `task-detail.svelte` says
 * `back={{ href: links.tasks(), label: 'Tasks' }}`, so a task opened from the DASHBOARD still
 * offered "Tasks". The parent is a guess about how you arrived, and it is wrong for every entry
 * point but one — and a task is reachable from the dashboard action queue, the tasks listing,
 * global search, the audit log and two chat rails.
 *
 * Only the APP can answer the question, because it needs the navigation history, and this package
 * may not import `$app/*` (it is consumed by three SvelteKit apps and a component library build;
 * every other app-coupled input here is injected the same way — see `task-detail`'s `links` and
 * `email-template-editor-page`'s `beforeNavigate`). So the app owns the store and publishes a live
 * view of it through this context; the shell reads it.
 *
 * An app that publishes nothing gets `undefined` and every back link stays exactly as it was —
 * which is how the admin and client portals are unaffected by any of this.
 */
import { getContext, setContext } from 'svelte';
import type { BackLink } from './types.js';

/** `Symbol.for`, matching the sidebar's own key convention: a globally registered symbol, so a test
 *  can seed the context map without importing a private binding. */
export const PAGE_BACK_KEY = Symbol.for('graphene-page-back');

/**
 * A LIVE view of the current back target.
 *
 * Deliberately an object with a getter rather than a plain value: `setContext` runs once, during
 * component init, so anything read out of it eagerly would freeze at whatever the first page was.
 * The getter is what carries the reactivity across the context boundary.
 */
export type PageBackSource = { readonly current: BackLink | null };

/** Publish the app's back target. Call once, at the top level of the root layout's `<script>` —
 *  `setContext` is only legal during init, so it must not sit inside an `{#if}` branch or an
 *  effect. */
export function setPageBack(source: PageBackSource): void {
	setContext(PAGE_BACK_KEY, source);
}

/** The app's back target, or `undefined` in an app that publishes none. */
export function getPageBack(): PageBackSource | undefined {
	return getContext(PAGE_BACK_KEY);
}
