<script lang="ts">
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import CheckIcon from '@lucide/svelte/icons/check';
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import type { Tenant } from './types.js';

	let {
		tenants,
		activeTenantId
	}: {
		/** When provided, renders the admin cross-tenant switcher. */
		tenants?: Tenant[];
		activeTenantId?: string;
	} = $props();

	const active = $derived(tenants?.find((t) => t.id === activeTenantId) ?? tenants?.[0]);
</script>

{#if tenants && tenants.length}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<!-- `--radius-chrome` is inherited from the topbar (see saas-topbar) so this chip has the
				     same capped corner as the nav rows instead of the full branded radius, which turns a
				     32px-tall chip into a pill. -->
				<button
					{...props}
					class="hover:bg-accent hover:text-foreground flex items-center gap-2 rounded-[var(--radius-chrome)] border px-2.5 py-1.5 text-sm font-medium"
				>
					<span class="truncate">{active?.name ?? 'Select tenant'}</span>
					<ChevronsUpDownIcon class="text-muted-foreground size-4" />
				</button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content class="w-64" align="start">
			<DropdownMenu.Label>Tenants</DropdownMenu.Label>
			<DropdownMenu.Separator />
			{#each tenants as tenant (tenant.id)}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a href="?tenant={tenant.id}" {...props} class="{props.class} justify-between">
							<span class="truncate">{tenant.name}</span>
							{#if tenant.id === active?.id}<CheckIcon class="size-4" />{/if}
						</a>
					{/snippet}
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/if}
