<script lang="ts">
	// The account's identity and its section strip live in the SIDEBAR, not on the page: the layout
	// load publishes `sidebarFocus` and the root layout renders it as a focused nav (the same
	// mechanism the client record uses). Each section renders the record's own header via
	// `AccountDetail`, so this layout adds no chrome of its own — an in-page tab strip is exactly
	// what this replaced.
	let { children } = $props();
</script>

{@render children()}
