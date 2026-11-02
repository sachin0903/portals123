<script lang="ts">
	import type { Snippet } from 'svelte';
	import EllipsisVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import { Button } from '../../ui/button/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';

	/**
	 * The page-level kebab on a book listing. The topbar crumb already names the screen, so a
	 * "New client" (or Requests, Generate, …) does not earn a heading row — it lives here, on the
	 * table, with the rest of the furniture.
	 *
	 * Render nothing when there are no items: an empty kebab is a dead control. The caller gates.
	 */
	let {
		children,
		label = 'Listing actions'
	}: {
		children: Snippet;
		label?: string;
	} = $props();
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button variant="ghost" size="icon" class="size-9" aria-label={label} {...props}>
				<EllipsisVerticalIcon class="size-4" />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-48">
		{@render children()}
	</DropdownMenu.Content>
</DropdownMenu.Root>
