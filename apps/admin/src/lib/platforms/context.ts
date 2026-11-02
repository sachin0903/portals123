import { getContext, setContext } from 'svelte';
import type { PlatformStore } from './store.svelte';

const KEY = Symbol('platform-store');

/**
 * The platform store lives in the `[slug]` layout, not in each page.
 *
 * Each of a platform's sections (overview, models, runs, query, lineage, …) is its own route now,
 * and the store holds view state the pages must not lose on navigation — the lineage canvas's
 * pan/zoom and node selection above all, which would be reset if the store were recreated per
 * page. A holder object is passed rather than the store itself so the layout can swap the store
 * when the slug changes and readers still see the new one.
 */
export interface PlatformStoreHolder {
	store: PlatformStore;
}

export function setPlatformStore(holder: PlatformStoreHolder): void {
	setContext(KEY, holder);
}

export function usePlatformStore(): PlatformStoreHolder {
	const holder = getContext<PlatformStoreHolder | undefined>(KEY);
	if (!holder) throw new Error('usePlatformStore() outside a platform layout');
	return holder;
}
