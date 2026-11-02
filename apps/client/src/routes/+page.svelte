<script lang="ts">
	import ComposedPage from '$lib/components/composed-page.svelte';
	import type { PageData } from './$types';

	// The home is a composed portal page (the tenant's 'home' definition or the default set's):
	// blocks + real RLS-scoped data through the shared @graphene/ui PortalPage grid — the same
	// renderer the branding studio previews. The onboarding CTA and the action-items card that
	// used to be hardcoded here are the onboardingProgress/actionItems blocks at the top of the
	// default home layout.
	let { data }: { data: PageData } = $props();
</script>

<ComposedPage composed={data.composed} />
