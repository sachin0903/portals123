<script lang="ts">
	import { provideCardDesigns } from '../../card-designs.js';
	import {
		brandingPreviewStyle,
		type Branding,
		type BrandingBlock,
		type BrandingPage
	} from '@graphene/auth';
	import HomeIcon from '@lucide/svelte/icons/home';
	import PieChartIcon from '@lucide/svelte/icons/pie-chart';
	import ReceiptIcon from '@lucide/svelte/icons/receipt';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import LayoutDashboardIcon from '@lucide/svelte/icons/layout-dashboard';
	import UsersIcon from '@lucide/svelte/icons/users';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import ClientShell from '../shell/client-shell.svelte';
	import SaaSShell from '../shell/saas-shell.svelte';
	import ClientHome from '../investment/client-home.svelte';
	import PortalPage from '../investment/portal-page.svelte';
	import type { PortalGridMetrics } from '../investment/masonry.js';
	import PreviewFrame from './preview-frame.svelte';
	import { PREVIEW_DEVICES, type PreviewDevice } from './preview-devices.js';
	import { StatusBadge, type StatusTone } from '../ui/status-badge/index.js';
	import * as Card from '../ui/card/index.js';
	import * as Table from '../ui/table/index.js';
	import type { Brand, NavItem, NavGroup } from '../shell/types.js';
	import { PAGE_ICONS, derivePortalNav } from './portal-pages.js';
	import { layoutFor, type Audience } from './page-audience.js';
	import { listingLinks } from './listing-links.js';
	import {
		demoVariantContext,
		personaVariantContext,
		resolveConcreteBlocks
	} from './pages-view.js';
	import { clientDemo } from '../../fixtures/demo.js';
	import type { Persona } from '../../fixtures/personas.js';

	// A live, faithful preview of a portal: the REAL app shell (ClientShell / SaaSShell) rendering
	// the real components with the shared demo data — not a mock. Themed by the tenant's branding
	// for the chosen scheme, and rendered inside PreviewFrame — an iframe whose internal viewport
	// is the chosen DEVICE's real size (preview-devices.ts), scaled to fit the column. Media
	// queries inside the frame evaluate at the simulated width, so the desktop preview genuinely
	// shows the desktop chrome and the iPhone one the mobile tab bar (a same-document box always
	// previewed the mobile layout at the column's ~450px), and the content SCROLLS. The iframe's
	// natural containment replaces the old `transform: translateZ(0)` containing-block hack, and
	// ClientShell's default `h-dvh` now measures the iframe viewport — exactly as in the real app.
	//
	// ── Why this is a PICTURE, not a second app ───────────────────────────────────────────────────
	// Rendering the real shell means rendering the real chrome, and the real chrome acts on the app
	// AROUND it. On /tenants/{id}/branding that app is the admin portal, so the preview shipped a
	// second `[aria-label="Colour theme"]` trigger that re-themed the portal you were editing in
	// (`ThemeToggle` writes the `gp-theme` cookie and toggles `.dark` on `document.documentElement`),
	// a sign-out form, links that navigated away from the editor, a rail toggle writing the shared
	// `sidebar_state` cookie, a second ⌘B binding on `window`, and a second writer of the root
	// `--content-inset-left`. A preview that silently mutates the app is worse than a slightly less
	// faithful preview, so:
	//
	//   1. `interactive={false}` — the shells swap every app-mutating control for a static stand-in
	//      that keeps the bar's exact composition, and switch off the out-of-subtree effects.
	//   2. The iframe — a separate document, so nothing inside can touch the host app's DOM,
	//      cookies-via-chrome or `window` bindings; PreviewFrame additionally suppresses activation
	//      (capture-phase click/submit/Enter/Space) so residual anchors can't navigate the frame
	//      itself. This replaces the old `inert` backstop, which also killed scrolling — the one
	//      capability the preview was missing.
	//
	// Both layers matter: (1) keeps dangerous nodes out of the DOM entirely; (2) covers what (1)
	// doesn't thread through.
	let {
		branding,
		scheme = 'light',
		portal,
		name,
		layout = null,
		pages = null,
		activeSlug = 'home',
		persona = null,
		audience,
		device = 'desktop',
		fontHrefs = [],
		onmeasure = null
	}: {
		branding: Branding;
		scheme?: 'light' | 'dark';
		portal: 'client' | 'platform';
		name?: string;
		/** Client-home layout (client portal only). LEGACY path — superseded by `pages`. */
		layout?: BrandingBlock[] | null;
		/** The working page set (client portal only). When provided the preview becomes page-aware:
		 * nav derives from the pages exactly as the real portal derives it, and the active page
		 * renders through PortalPage with audience-resolved, concretely-varianted blocks. Absent ⇒
		 * the legacy `layout` path renders ClientHome unchanged. */
		pages?: BrandingPage[] | null;
		/** Which page the frame shows (by slug; unknown ⇒ the first page). */
		activeSlug?: string;
		/** The studio persona whose data + counts feed the page (null ⇒ the shared demo fixture). */
		persona?: Persona | null;
		/** Pins the audience whose layout renders; absent ⇒ the persona's own, else individual. */
		audience?: Audience;
		/** Which simulated viewport the frame renders at. Default keeps existing consumers on a
		 * real desktop (1920×1080) preview with no prop changes. */
		device?: PreviewDevice;
		/** Google Fonts stylesheet URLs to load INSIDE the frame — the iframe has its own head, so
		 * fonts the editor loaded into the host page don't reach it on their own. */
		fontHrefs?: string[];
		/** Reports the composed page's real measured grid (pages path only) so the studio's layout
		 * builder can draw the arrangement this frame is showing rather than one derived from the
		 * authored spans alone. */
		onmeasure?: ((metrics: PortalGridMetrics) => void) | null;
	} = $props();

	// The account cards inside the frame wear the design being edited, not the product defaults.
	provideCardDesigns(() => (portal === 'client' ? branding.card_designs : null));

	const deviceSpec = $derived(PREVIEW_DEVICES[device]);

	const firm = $derived(name?.trim() || 'Your firm');
	const brand = $derived<Brand>({
		name: firm,
		logoUrl: branding.logo_url ?? undefined,
		logoDarkUrl: branding.logo_dark_url ?? undefined,
		iconUrl: branding.icon_url ?? undefined,
		logoVariant: branding.logo_variant ?? undefined,
		tagline: branding.login?.tagline ?? undefined,
		legal: branding.login?.legal ?? undefined
	});

	// The scoped theme: the chosen palette's tokens + shared radius/font/glass. Combined with the
	// `dark` class this renders exactly as the real portal would for this firm and scheme.
	// The platform portal is a staff tool: paper, type and material are the product's — the real
	// portal passes `surface: 'fixed'`, so the preview must too or it shows a generated re-theme
	// the live portal withholds.
	const surfaceStyle = $derived(
		brandingPreviewStyle(branding, scheme, {
			surface: portal === 'platform' ? 'fixed' : 'themed'
		})
	);

	const demoUser = { email: 'james.whitfield@example.com' };

	const clientNav: NavItem[] = [
		{ label: 'Home', href: '/', icon: HomeIcon },
		{ label: 'Holdings', href: '/holdings', icon: PieChartIcon },
		{ label: 'Activity', href: '/activity', icon: ReceiptIcon },
		{ label: 'Documents', href: '/documents', icon: FileTextIcon }
	];

	// ── Page-aware client preview (pages supplied) ─────────────────────────────
	// Nav mirrors the real portal exactly: derivePortalNav (order, ≤6 cap, hidden pages dropped)
	// mapped through PAGE_ICONS, so the frame and the live app cannot disagree about the tab bar.
	const pageSet = $derived(portal === 'client' && pages && pages.length > 0 ? pages : null);
	const activePage = $derived(
		pageSet ? (pageSet.find((p) => p.slug === activeSlug) ?? pageSet[0]) : null
	);
	const previewAudience = $derived<Audience>(audience ?? persona?.audience ?? 'individual');
	const pagesNav = $derived<NavItem[]>(
		derivePortalNav(pageSet).map((item) => ({
			label: item.label,
			href: item.href,
			icon: PAGE_ICONS[item.iconName] ?? PAGE_ICONS['layout-grid']
		}))
	);
	const activePath = $derived(
		activePage ? (activePage.slug === 'home' ? '/' : `/${activePage.slug}`) : '/'
	);
	// The cards' "View all" affordances, decided from the DRAFT page set the same way the live
	// portal decides them — so a firm deleting its Holdings page watches the link leave the card.
	const pageLinks = $derived(
		pageSet && activePage ? listingLinks(pageSet, previewAudience, activePage.slug) : null
	);
	// PortalPage expects CONCRETE variants (the real loader resolves 'auto' before render), so the
	// preview resolves them the same way — from the persona's counts and data, or the shared demo
	// fixture's when no persona is supplied, so the resolver always describes what the frame shows.
	const pageBlocks = $derived.by<BrandingBlock[]>(() => {
		if (!activePage) return [];
		const blocks = layoutFor(activePage, previewAudience);
		const ctx = persona
			? personaVariantContext(persona, activePage.slug, previewAudience)
			: demoVariantContext(clientDemo, activePage.slug, previewAudience);
		return resolveConcreteBlocks(blocks, ctx);
	});
	const platformNav: NavGroup[] = [
		{
			items: [
				{ label: 'Dashboard', href: '/', icon: LayoutDashboardIcon },
				{ label: 'Clients', href: '/clients', icon: UsersIcon },
				{ label: 'Compliance', href: '/compliance', icon: ShieldCheckIcon }
			]
		}
	];

	// Platform home is a fixed dashboard (no layout system) — mirror apps/platform/+page.svelte.
	const stats = [
		{ label: 'Clients', value: '128', change: '+6 this month', positive: true },
		{ label: 'Assets under advice', value: '£84.2m', change: '+2.1% this quarter', positive: true },
		{ label: 'Reviews due (30d)', value: '17', change: '4 overdue', positive: false }
	];
	const clients = [
		{ name: 'Margaret Cole', email: 'm.cole@example.com', status: 'Active', portfolio: '£1.24m' },
		{ name: 'David Osei', email: 'd.osei@example.com', status: 'Active', portfolio: '£860k' },
		{ name: 'Priya Nair', email: 'p.nair@example.com', status: 'Review due', portfolio: '£2.10m' }
	];
	// The preview shows the PLATFORM shell, so its badges must read as the real product's do. This
	// mapped Active to `secondary` — a NEUTRAL surface — which is the "secondary meaning good"
	// mistake `docs/design-language.md` names by name, and it made a healthy client look identical
	// to a draft one in the very picture a firm judges its branding by.
	const statusTone = (s: string): StatusTone =>
		s === 'Active' ? 'success' : s === 'Review due' ? 'warning' : 'neutral';
</script>

<PreviewFrame
	width={deviceSpec.width}
	height={deviceSpec.height}
	{surfaceStyle}
	{scheme}
	{fontHrefs}
	label="{portal === 'client'
		? 'Client'
		: 'Platform'} portal preview for {firm} ({scheme} theme, {deviceSpec.label})"
>
	{#if portal === 'client'}
		<!-- No heightClass override: the default `h-dvh` measures the IFRAME viewport, which is
		     exactly how the shell sizes itself in the real portal. -->
		<ClientShell
			user={demoUser}
			nav={pageSet ? pagesNav : clientNav}
			pathname={pageSet ? activePath : '/'}
			{brand}
			interactive={false}
		>
			{#if pageSet && activePage}
				<!-- finish rides through so look:'auto' blocks preview the same design the live
				     portal resolves — a ledger-finish firm must SEE the chrome-less looks. -->
				<PortalPage
					blocks={pageBlocks}
					data={persona?.data ?? null}
					interactive={false}
					pageSlug={activePage.slug}
					links={pageLinks}
					finish={branding.finish}
					{onmeasure}
				/>
			{:else}
				<ClientHome {layout} interactive={false} />
			{/if}
		</ClientShell>
	{:else}
		<SaaSShell
			portal="Platform"
			user={demoUser}
			nav={platformNav}
			pathname="/"
			{brand}
			interactive={false}
		>
			<div class="space-y-6">
				<div>
					<h1 class="text-3xl font-semibold tracking-tight">Dashboard</h1>
					<p class="text-muted-foreground mt-1">Tools for advisers, compliance and paraplanning.</p>
				</div>
				<div class="grid gap-4 sm:grid-cols-3">
					{#each stats as stat (stat.label)}
						<Card.Root glass>
							<Card.Header class="gap-1">
								<Card.Description>{stat.label}</Card.Description>
								<Card.Title class="text-3xl tabular-nums">{stat.value}</Card.Title>
								<p class="text-xs font-medium {stat.positive ? 'text-positive' : 'text-negative'}">
									{stat.change}
								</p>
							</Card.Header>
						</Card.Root>
					{/each}
				</div>
				<Card.Root glass>
					<Card.Header>
						<Card.Title>Clients</Card.Title>
						<Card.Description>Example data for this preview.</Card.Description>
					</Card.Header>
					<Card.Content>
						<Table.Root>
							<Table.Header>
								<Table.Row>
									<Table.Head>Name</Table.Head>
									<Table.Head>Email</Table.Head>
									<Table.Head>Status</Table.Head>
									<Table.Head class="text-right">Portfolio</Table.Head>
								</Table.Row>
							</Table.Header>
							<Table.Body>
								{#each clients as client (client.email)}
									<Table.Row>
										<Table.Cell class="font-medium">{client.name}</Table.Cell>
										<Table.Cell class="text-muted-foreground">{client.email}</Table.Cell>
										<Table.Cell>
											<StatusBadge tone={statusTone(client.status)}>{client.status}</StatusBadge>
										</Table.Cell>
										<Table.Cell class="text-right tabular-nums">{client.portfolio}</Table.Cell>
									</Table.Row>
								{/each}
							</Table.Body>
						</Table.Root>
					</Card.Content>
				</Card.Root>
			</div>
		</SaaSShell>
	{/if}
</PreviewFrame>
