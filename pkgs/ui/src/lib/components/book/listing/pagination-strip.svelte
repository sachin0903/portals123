<script lang="ts">
	import { cn } from '../../../utils.js';
	import UiPagination from '../../ui/pagination/pagination.svelte';
	import ExportMenu from './export-menu.svelte';

	let {
		label,
		page = $bindable(1),
		pageCount,
		href,
		exportHref,
		class: className
	}: {
		label: string;
		page?: number;
		pageCount: number;
		href?: (page: number) => string;
		exportHref?: string;
		class?: string;
	} = $props();
</script>

<!-- One footer for both URL-backed and already-loaded listings: range left, navigation and export
     right. Keeping this shape here prevents each local pager from drifting back to centred. -->
<div
	class={cn('-mx-4 flex shrink-0 items-center gap-3 border-t px-4 py-3', className)}
	data-slot="listing-pagination"
>
	<p class="text-muted-foreground shrink-0 text-xs tabular-nums" aria-live="polite">{label}</p>
	<div class="ml-auto min-w-0"><UiPagination bind:page {pageCount} {href} /></div>
	{#if exportHref}
		<div class="shrink-0"><ExportMenu href={exportHref} label="Export" /></div>
	{/if}
</div>
