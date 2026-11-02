<script lang="ts">
	// The acting-on-behalf / read-only statement lives in the top bar (the root layout's `notice`
	// slot), where it stays visible without spending a band of every page. This layout only loads.
	let { children } = $props();
</script>

{@render children?.()}
