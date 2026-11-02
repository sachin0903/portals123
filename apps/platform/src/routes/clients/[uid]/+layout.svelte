<script lang="ts">
	// The client's identity and its section strip live in the SIDEBAR, not on the page: the layout
	// load publishes `sidebarFocus` and the root layout renders it as a focused nav (mirroring
	// apps/admin/src/routes/tenants/[id]). Each section renders its own header, so this layout adds
	// no chrome of its own — a second in-page tab strip is exactly what the restructure removed.
	//
	// This client's OWN unread dot (the one in the section strip) is kept live by the ROOT layout,
	// not here — it polls `GET /chats/unread?contact_id=` itself off `sidebarFocus.liveUnreadContactId`
	// (see `routes/+layout.svelte`). It used to live in this component as an `invalidate()` poll, but
	// that reran THIS layout's `load` on every tick, which every section below it (Compliance,
	// Documents, ...) also re-ran because they all call `parent()` — thirty seconds of network traffic
	// on every open client tab for a dot most of those sections have nothing to do with.
	let { children } = $props();
</script>

{@render children()}
