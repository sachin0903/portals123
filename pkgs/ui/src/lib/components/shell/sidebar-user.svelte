<script lang="ts">
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import * as Avatar from '../ui/avatar/index.js';
	import * as Sidebar from '../ui/sidebar/index.js';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import { ThemeToggle } from '../ui/theme-toggle/index.js';
	import type { NavItem } from './types.js';

	let {
		user,
		menuItems = [],
		interactive = true
	}: {
		user: { email: string; name?: string };
		menuItems?: NavItem[];
		/**
		 * `false` ⇒ rendered inside the branding preview. The row keeps its exact appearance but loses
		 * the dropdown — and with it the `POST /logout` form, which is the single most dangerous thing
		 * a "live preview of the real shell" can contain: a mis-click in the branding editor would have
		 * signed the operator out. A preview shows chrome; it does not own actions.
		 */
		interactive?: boolean;
	} = $props();

	const initial = $derived((user.name ?? user.email).charAt(0).toUpperCase());
	const primary = $derived(user.name ?? user.email);
</script>

{#snippet row(props: Record<string, unknown>)}
	<!-- `--radius-chrome` is inherited from Sidebar.Root (see app-sidebar): the account row
	     and its avatar take the chrome's capped corner, not the full branded radius. -->
	<Sidebar.MenuButton
		size="lg"
		{...props}
		class="data-[state=open]:bg-sidebar-accent hover:bg-accent rounded-[var(--radius-chrome)]"
	>
		<Avatar.Root class="size-8 rounded-[var(--radius-chrome)]">
			<Avatar.Fallback
				class="bg-sidebar-primary text-sidebar-primary-foreground rounded-[var(--radius-chrome)] text-xs font-semibold"
			>
				{initial}
			</Avatar.Fallback>
		</Avatar.Root>
		<div class="grid flex-1 text-left leading-tight group-data-[collapsible=icon]:hidden">
			<span class="truncate text-sm font-medium">{primary}</span>
			{#if user.name}
				<span class="text-sidebar-foreground/50 truncate text-xs">{user.email}</span>
			{/if}
		</div>
		<ChevronsUpDownIcon
			class="text-sidebar-foreground/50 ml-auto size-4 group-data-[collapsible=icon]:hidden"
		/>
	</Sidebar.MenuButton>
{/snippet}

{#if !interactive}
	<Sidebar.Menu>
		<Sidebar.MenuItem>
			{@render row({})}
		</Sidebar.MenuItem>
	</Sidebar.Menu>
{:else}
	<Sidebar.Menu>
		<Sidebar.MenuItem>
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						{@render row(props)}
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Content side="right" align="end" sideOffset={8} class="w-56">
					<DropdownMenu.Label class="truncate font-normal">{user.email}</DropdownMenu.Label>
					<DropdownMenu.Separator />
					<DropdownMenu.Item>
						{#snippet child({ props })}
							<a href="/account" {...props}>
								<UserRoundIcon class="size-4" />
								Account
							</a>
						{/snippet}
					</DropdownMenu.Item>
					{#each menuItems as item (item.href)}
						<DropdownMenu.Item>
							{#snippet child({ props })}
								<a href={item.href} {...props}>
									{#if item.icon}<item.icon class="size-4" />{/if}
									{item.label}
								</a>
							{/snippet}
						</DropdownMenu.Item>
					{/each}
					<ThemeToggle submenu />
					<DropdownMenu.Separator />
					<form method="POST" action="/logout">
						<DropdownMenu.Item>
							{#snippet child({ props })}
								<button type="submit" {...props} class="{props.class} w-full">
									<LogOutIcon class="size-4" />
									Sign out
								</button>
							{/snippet}
						</DropdownMenu.Item>
					</form>
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		</Sidebar.MenuItem>
	</Sidebar.Menu>
{/if}
