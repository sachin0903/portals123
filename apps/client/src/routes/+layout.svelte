<script lang="ts">
	import '../app.css';
	import AuthShell from '@graphene/ui/auth-shell';
	import { Toaster } from '@graphene/ui/toaster';
	import { navigating, page } from '$app/state';
	import RumBeacon from '@graphene/rum/beacon';
	import { documentTitle } from '$lib/document-title';

	let { data, children } = $props();

	/**
	 * Routes that ARE the auth flow, or the identity-photo link. They must never wear the app
	 * chrome, and they must not wait on it: the photo page is opened on a phone, and loading the
	 * signed-in shell first left that page painted but unable to take a tap.
	 */
	const AUTH_ROUTES = new Set([
		'login',
		'logout',
		'mfa',
		'set-password',
		'forgot-password',
		'verify',
		'i'
	]);
	const isAuthRoute = $derived(AUTH_ROUTES.has(page.url.pathname.split('/')[1] ?? ''));

	const brand = $derived({
		name: data.appName ?? undefined,
		logoUrl: data.branding?.logo_url ?? undefined,
		logoDarkUrl: data.branding?.logo_dark_url ?? undefined,
		iconUrl: data.branding?.icon_url ?? undefined,
		logoVariant: data.branding?.logo_variant ?? undefined,
		tagline: data.branding?.login?.tagline ?? undefined,
		legal: data.branding?.login?.legal ?? undefined
	});

	const brandName = $derived(data.appName ?? 'Graphene');
	const pageLabels = $derived(new Map((data.branding?.pages ?? []).map((p) => [p.slug, p.label])));
	// Where a tap is already heading. `page.url` is still the page being left for the whole of the
	// destination's load, so the browser tab follows this and moves on the tap.
	const pendingHref = $derived(navigating.to?.url.pathname ?? null);
	const pageTitle = $derived(
		documentTitle(pendingHref ?? page.url.pathname, brandName, pageLabels)
	);
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>
<RumBeacon portal="client" />

{#if isAuthRoute}
	<AuthShell {brand}>
		{@render children()}
	</AuthShell>
	<Toaster position="top-center" />
{:else}
	{#await import('./portal-chrome.svelte') then { default: PortalChrome }}
		<PortalChrome {data}>
			{@render children()}
		</PortalChrome>
	{/await}
{/if}
