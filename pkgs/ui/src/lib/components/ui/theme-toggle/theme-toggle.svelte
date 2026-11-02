<script lang="ts">
	/**
	 * Light / dark / system switcher. `<ThemeToggle />` — no required props, so it can be dropped
	 * straight into a topbar.
	 *
	 * The resolution rules live in `@graphene/auth/theme` (choice > tenant branding mode > OS) and are
	 * shared with the `handle` hook that server-renders the scheme and the blocking head script. This
	 * component owns only the UI and the write side:
	 *
	 *   - the TRIGGER icon is pure CSS (`dark:` variants), so it is already correct in the SSR paint —
	 *     no post-hydration flicker, and nothing to seed from the server;
	 *   - the SELECTED state is the effective choice, read on mount off the `<html>` attributes the
	 *     server rendered (the only place that knows the cookie and the tenant's mode);
	 *   - choosing writes the `gp-theme` cookie (so the next SSR paint is right) plus the attribute,
	 *     then re-resolves. The head script's `prefers-color-scheme` listener reads the same
	 *     attribute, so it agrees with a runtime change instead of fighting it.
	 */
	import SunIcon from '@lucide/svelte/icons/sun';
	import MoonIcon from '@lucide/svelte/icons/moon';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import {
		CHOICE_ATTR,
		DARK_CLASS,
		MODE_ATTR,
		effectiveChoice,
		parseTenantMode,
		parseThemeChoice,
		resolveScheme,
		themeCookie,
		type ThemeChoice
	} from '@graphene/auth';
	import { onMount } from 'svelte';
	import { Button, type ButtonSize, type ButtonVariant } from '../button/index.js';
	import * as DropdownMenu from '../dropdown-menu/index.js';
	import Segmented from '../segmented/segmented.svelte';

	let {
		variant = 'ghost',
		size = 'icon-sm',
		align = 'end',
		submenu = false,
		inline = false,
		class: className
	}: {
		variant?: ButtonVariant;
		size?: ButtonSize;
		align?: 'start' | 'center' | 'end';
		/** Render inside an existing account dropdown rather than as a separate icon trigger. */
		submenu?: boolean;
		/** Render as a Light / Dark / System segmented control, for a settings page where the
		 *  choice is laid out rather than tucked behind an icon (the client portal's profile). */
		inline?: boolean;
		class?: string;
	} = $props();

	const OPTIONS: { value: ThemeChoice; label: string; icon: typeof SunIcon }[] = [
		{ value: 'light', label: 'Light', icon: SunIcon },
		{ value: 'dark', label: 'Dark', icon: MoonIcon },
		{ value: 'system', label: 'System', icon: MonitorIcon }
	];

	// Seeded on mount from the server-rendered attributes; 'system' is the safe pre-mount default
	// (it is also what a portal with no branding mode and no cookie resolves to).
	let choice = $state<ThemeChoice>('system');

	/** Re-derive the scheme from the `<html>` attributes + the live OS preference and apply it. */
	function apply() {
		const root = document.documentElement;
		const scheme = resolveScheme({
			choice: parseThemeChoice(root.getAttribute(CHOICE_ATTR)),
			mode: parseTenantMode(root.getAttribute(MODE_ATTR)),
			prefersDark: window.matchMedia('(prefers-color-scheme: dark)').matches
		});
		root.classList.toggle(DARK_CLASS, scheme === 'dark');
	}

	function select(next: string) {
		const value = parseThemeChoice(next);
		if (!value) return;
		choice = value;
		const root = document.documentElement;
		root.setAttribute(CHOICE_ATTR, value);
		// Not httpOnly by design — the server reads it to render the right scheme first time.
		document.cookie = themeCookie(value, { secure: window.location.protocol === 'https:' });
		apply();
	}

	onMount(() => {
		const root = document.documentElement;
		choice = effectiveChoice({
			choice: parseThemeChoice(root.getAttribute(CHOICE_ATTR)),
			mode: parseTenantMode(root.getAttribute(MODE_ATTR))
		});
	});
</script>

{#snippet schemeIcon()}
	<SunIcon class="size-4 dark:hidden" aria-hidden="true" />
	<MoonIcon class="hidden size-4 dark:block" aria-hidden="true" />
{/snippet}

{#snippet options()}
	<DropdownMenu.RadioGroup value={choice} onValueChange={select}>
		{#each OPTIONS as option (option.value)}
			<DropdownMenu.RadioItem value={option.value}>
				<option.icon class="text-muted-foreground size-4" aria-hidden="true" />
				{option.label}
			</DropdownMenu.RadioItem>
		{/each}
	</DropdownMenu.RadioGroup>
{/snippet}

{#if inline}
	<!-- `lg`: laid out on a settings page that is tapped, each segment is its own 44px target. -->
	<Segmented
		options={OPTIONS}
		value={choice}
		onValueChange={select}
		label="Colour theme"
		size="lg"
		class={className}
	/>
{:else if submenu}
	<DropdownMenu.Sub>
		<DropdownMenu.SubTrigger>
			{@render schemeIcon()}
			Theme
		</DropdownMenu.SubTrigger>
		<DropdownMenu.SubContent class="w-40">
			<DropdownMenu.Label>Theme</DropdownMenu.Label>
			<DropdownMenu.Separator />
			{@render options()}
		</DropdownMenu.SubContent>
	</DropdownMenu.Sub>
{:else}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button {...props} {variant} {size} class={className} aria-label="Colour theme">
					<!-- Reflects the ACTIVE scheme, driven by the `dark` class alone, so it is correct in
				     the server-rendered paint without any client state. `dark:` is safe here because
				     theme.css binds it to the class — `@custom-variant dark (&:where(.dark, .dark *))`
				     — rather than leaving it on Tailwind v4's default
				     `@media (prefers-color-scheme: dark)`, which would have shown the wrong icon to
				     anyone whose chosen scheme differs from their OS. This pair doubles as the
				     cheapest live check that the variant is still bound to the class. -->
					{@render schemeIcon()}
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content {align} class="w-40">
			<DropdownMenu.Label>Theme</DropdownMenu.Label>
			<DropdownMenu.Separator />
			{@render options()}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/if}
