<script lang="ts">
	import type { BrandingBlock } from '@graphene/auth';
	import { defaultClientLayout } from '../branding/client-blocks.js';
	import PortalPage from './portal-page.svelte';
	import type { ClientHomeData } from './types.js';

	// The client-portal home — now a thin wrapper over the generalised PortalPage renderer, kept
	// so its long-standing call sites (the client app's home, the branding studio's shell/layout
	// previews) need no change. Demo gating moved into resolveSlices (slices.ts): `data === null`
	// (the preview) renders demo fixtures wholesale; a real client's missing slices render empty
	// states — the old per-slice `data?.x ?? clientDemo.x` here leaked fixture documents/news/
	// adviser to signed-in clients, and that behaviour change is the point.
	let {
		layout = null,
		interactive = true,
		data = null
	}: {
		layout?: BrandingBlock[] | null;
		interactive?: boolean;
		data?: ClientHomeData | null;
	} = $props();

	const blocks = $derived(layout && layout.length > 0 ? layout : defaultClientLayout());
</script>

<PortalPage {blocks} {data} {interactive} pageSlug="home" />
