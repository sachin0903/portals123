<script lang="ts">
	import { bankLogo } from '../bank-logos';

	let { name, bic }: { name: string; bic: string | null } = $props();

	const logo = $derived(bankLogo(bic, name));
	/** The logo whose request failed, so the mark shows initials instead of a broken image. */
	let failedUrl = $state<string | null>(null);
	const failed = $derived(logo != null && failedUrl === logo.url);
	/** First letters of the first two significant words, for banks without a logo here. */
	const initials = $derived(
		name
			.split(/\s+/)
			.filter((word) => word && !['THE', 'OF', 'AND', '&'].includes(word.toUpperCase()))
			.slice(0, 2)
			.map((word) => word[0]!.toUpperCase())
			.join('')
	);
</script>

{#if logo && !failed}
	<img
		src={logo.url}
		alt={name}
		title={name}
		width="20"
		height="20"
		class="size-5 shrink-0 rounded-full"
		loading="lazy"
		decoding="async"
		referrerpolicy="no-referrer"
		onerror={() => (failedUrl = logo.url)}
	/>
{:else}
	<span
		class="bg-muted text-muted-foreground inline-flex size-5 shrink-0 items-center justify-center rounded-md text-[0.625rem] font-semibold tracking-tight"
		title={name}
	>
		<span aria-hidden="true">{initials}</span>
		<span class="sr-only">{name}</span>
	</span>
{/if}
