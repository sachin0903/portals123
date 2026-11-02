<script lang="ts">
	/** `<svelte:head>` cannot sit inside a block or snippet, so Streamed pages mount this instead. */
	let { title }: { title: string } = $props();
</script>

<svelte:head><title>{title}</title></svelte:head>
