<script lang="ts">
	// The tenant context (name + section nav) lives in the sidebar (see the root layout,
	// which scopes to the tenant from page.data.tenant). Sub-pages render their own headers.
	let { children } = $props();
</script>

{@render children()}
