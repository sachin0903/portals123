<script lang="ts">
	// The floating bar for a documents selection. A Delete shows when it has a callback and targets.
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../../ui/button/index.js';
	import { Separator } from '../../ui/separator/index.js';

	let {
		count,
		documents,
		files,
		ondeletedocuments,
		ondeletefiles,
		onclear
	}: {
		/** Everything selected; `documents` leaves protected ones out. */
		count: number;
		documents: number;
		files: number;
		ondeletedocuments?: () => void;
		ondeletefiles?: () => void;
		onclear: () => void;
	} = $props();
</script>

{#if count > 0}
	<div
		class="bg-popover ring-foreground/10 fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-3 rounded-full px-4 py-2 text-sm shadow-lg ring-1"
	>
		<span class="font-medium">{count} selected</span>
		<Separator orientation="vertical" class="h-4" />
		{#if documents > 0 && ondeletedocuments}
			<Button variant="ghost" size="sm" class="text-destructive" onclick={ondeletedocuments}>
				<Trash2Icon class="size-4" /> Delete {documents}
			</Button>
		{/if}
		{#if files > 0 && ondeletefiles}
			<Button variant="ghost" size="sm" class="text-destructive" onclick={ondeletefiles}>
				<Trash2Icon class="size-4" /> Delete {files}
				{files === 1 ? 'file' : 'files'}
			</Button>
		{/if}
		<Button variant="ghost" size="sm" class="text-muted-foreground" onclick={onclear}>Clear</Button>
	</div>
{/if}
