<script lang="ts">
	// A firm as the AML console names it: its mark beside its name. The mark is the platform's own
	// `Logo` mark — the firm's icon from its platform-portal branding, or the default mark the firm's
	// staff see in their own sidebar when it has set none.
	import { Logo } from '@graphene/ui';

	let {
		name,
		iconUrl = null,
		href = null,
		size = 'sm',
		class: className = ''
	}: {
		name: string;
		iconUrl?: string | null;
		/** Where the name links to, when it should. */
		href?: string | null;
		size?: 'sm' | 'md';
		class?: string;
	} = $props();
</script>

<span class="inline-flex min-w-0 items-center gap-2 {className}">
	<Logo
		variant="mark"
		iconSrc={iconUrl ?? undefined}
		{name}
		markClass="{size === 'md' ? 'size-7' : 'size-5'} shrink-0 rounded-md"
	/>
	{#if href}
		<a {href} class="truncate hover:underline">{name}</a>
	{:else}
		<span class="truncate">{name}</span>
	{/if}
</span>
