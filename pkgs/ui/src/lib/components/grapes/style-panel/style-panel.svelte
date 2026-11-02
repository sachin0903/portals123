<script lang="ts">
	// `custom: true` suppresses GrapesJS's VIEW only, so the mounting editor's own safety still applies
	// (`read.ts`). Models are re-read, never cached: a value changes from four directions.
	import InspectorSector from '../../ui/inspector-sector/inspector-sector.svelte';
	import PropertyControl from './property-control.svelte';
	import BorderControl from './border-control.svelte';
	import { panelRows } from './borders.js';
	import { sectorSet, sectorViews, type StyleManagerLike } from './read.js';

	let {
		styleManager,
		revision = 0,
		brandColours = [],
		disabled = false,
		onchange
	}: {
		styleManager?: StyleManagerLike;
		revision?: number;
		brandColours?: readonly string[];
		disabled?: boolean;
		/** Called after any write, so the caller can bump `revision` and sync content upward. */
		onchange: () => void;
	} = $props();

	// `revision` is taken as an ARGUMENT: a bare `revision;` line gets tidied away and the panel freezes.
	function readSectors(_revision: number, sm?: StyleManagerLike) {
		return sectorViews(sm);
	}
	const sectors = $derived(readSectors(revision, styleManager));

	// Not pushed into `Sector.setOpen`, so a collapse never reaches a template's blob; absent = declared.
	let override = $state<Record<string, boolean>>({});
	function isOpen(id: string, declared: boolean): boolean {
		return override[id] ?? declared;
	}
	function toggle(id: string, open: boolean): void {
		override = { ...override, [id]: open };
	}
</script>

{#each sectors as sector (sector.id)}
	<!-- `InspectorSector` owns the header, as the curated inspectors do, so the rail is ONE accordion. -->
	<InspectorSector
		label={sector.label}
		set={sectorSet(sector)}
		open={isOpen(sector.id, sector.open)}
		onOpenChange={(o) => toggle(sector.id, o)}
	>
		<div class="flex flex-col px-2 pt-1 pb-2" data-sector={sector.id}>
			<!-- `panelRows` replaces the five border composites with one row where the first sat, driven by
			     what the rail holds rather than a list of sector ids. -->
			{#each panelRows(sector.properties) as row (row.key)}
				{#if row.kind === 'borders'}
					<BorderControl family={row.family} {brandColours} {disabled} {onchange} />
				{:else}
					<PropertyControl view={row.view} {brandColours} {disabled} {onchange} />
				{/if}
			{/each}
		</div>
	</InspectorSector>
{/each}
