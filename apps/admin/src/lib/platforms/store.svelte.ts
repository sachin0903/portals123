// Shared runes state for one platform page — the lineage canvas, detail rail
// and toolbar all read/write the same instance (one per slug, kept across data
// reloads via replace()).

import { SvelteSet } from 'svelte/reactivity';
import {
	applyDaemonEvent,
	enrichDataset,
	retainCatalogue,
	type ApiCatalogue,
	type ApiEvent
} from './live';
import type { PlatformDataset } from './types';

export class PlatformStore {
	data = $state({} as PlatformDataset);

	readonly byName = $derived(new Map(this.data.models.map((m) => [m.name, m])));
	/** Reverse edges, derived from deps. */
	readonly downstream = $derived.by(() => {
		const map = new Map<string, string[]>(this.data.models.map((m) => [m.name, []]));
		for (const m of this.data.models) {
			for (const dep of m.deps) map.get(dep)?.push(m.name);
		}
		return map;
	});

	// No `tab` here any more: a platform's sections are routes (the sidebar drills into them), so
	// the URL holds that state. What remains is view state the routes must not lose — hence the
	// store living in the [slug] layout rather than in each page.
	selected = $state('');
	/** Bumped by `focus` so the canvas recentres even when the selection did not change. */
	focusTick = $state(0);
	/** Models expanded to column-level lineage on the graph. */
	expanded = new SvelteSet<string>();

	selectedModel = $derived.by(() => this.byName.get(this.selected) ?? null);
	planCounts = $derived.by(() => {
		const counts = { breaking: 0, nonbreaking: 0, added: 0, unchanged: 0 };
		for (const m of this.data.models) counts[m.state]++;
		return counts;
	});

	constructor(dataset: PlatformDataset) {
		this.data = dataset;
		this.selected = defaultSelection(dataset);
	}

	/** Swap in a freshly loaded dataset (same platform) without losing UI state —
	 *  form actions/invalidations rerun the page load, and recreating the store
	 *  would reset the active tab, selection and expansion (and orphan any
	 *  component still holding the old instance). */
	replace(dataset: PlatformDataset) {
		this.data = retainCatalogue(this.data, dataset);
		const names = new Set(dataset.models.map((m) => m.name));
		for (const name of [...this.expanded]) {
			if (!names.has(name)) this.expanded.delete(name);
		}
		if (!names.has(this.selected)) this.selected = defaultSelection(dataset);
	}

	select(name: string) {
		if (this.byName.has(name)) this.selected = name;
	}

	/** Select a model and ask the canvas to centre it. */
	focus(name: string) {
		this.select(name);
		if (this.selected === name) this.focusTick += 1;
	}

	applyDaemonEvent(event: ApiEvent) {
		this.data = applyDaemonEvent(this.data, event);
	}

	/** SQL, column lineage and row counts, once the catalogue request lands. */
	applyCatalogue(catalogue: ApiCatalogue) {
		this.data = enrichDataset(this.data, catalogue);
	}

	toggle(name: string) {
		if (this.expanded.has(name)) this.expanded.delete(name);
		else this.expanded.add(name);
	}

	expandAll() {
		for (const m of this.data.models) this.expanded.add(m.name);
	}

	collapseAll() {
		this.expanded.clear();
	}
}

// Default selection: the last-layer model with the most upstream context.
// (A live dataset can be empty while its pipeline daemon is unreachable.)
function defaultSelection(dataset: PlatformDataset): string {
	const maxLayer = dataset.models.length ? Math.max(...dataset.models.map((m) => m.layer)) : 0;
	return dataset.models.find((m) => m.layer === maxLayer)?.name ?? dataset.models[0]?.name ?? '';
}
