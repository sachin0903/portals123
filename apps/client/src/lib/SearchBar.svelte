<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		CLIENT_HEADER_BUTTON_CLASS,
		CLIENT_HEADER_ICON_CLASS,
		SpotlightSearch
	} from '@graphene/ui';
	import SearchIcon from '@lucide/svelte/icons/search';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import ChartPieIcon from '@lucide/svelte/icons/chart-pie';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import NewspaperIcon from '@lucide/svelte/icons/newspaper';

	import { ROUTES, SearchClient, searchUrl, SEARCH_SCOPES } from '$lib/search';

	/**
	 * The client portal's search: the round glass header button, and the portal's shared
	 * `SpotlightSearch` palette with THIS portal's policy — its categories, where a hit routes
	 * (`ROUTES`), and the active-client scoping on the URL. The palette also answers the browse
	 * bar's search button on the Docs and Insights pages (`openSearchPalette('document')`), which
	 * opens it here already narrowed to that category.
	 */

	const ICONS: Record<string, typeof SearchIcon> = {
		account: LandmarkIcon,
		holding: ChartPieIcon,
		transaction: ArrowLeftRightIcon,
		document: FileTextIcon,
		insight: NewspaperIcon
	};
	const TYPE_LABELS: Record<string, string> = {
		account: 'Account',
		holding: 'Holding',
		transaction: 'Transaction',
		document: 'Document',
		insight: 'Insight'
	};

	/** Which client this login is currently acting for, so the hits are that client's alone. A
	 *  login linked to one client still passes it; absent it, the API answers over the whole
	 *  contact scope, which for a multi-client login is somebody else's documents. */
	let { activeContactId = null }: { activeContactId?: number | null } = $props();

	let open = $state(false);

	// Sequence-guarded (see $lib/search) so a slow earlier response can't overwrite a newer one.
	// The URL builder is the shared one with the active client appended — `SearchClient` takes it
	// as an argument precisely so a portal can decide what it is asking about.
	const client = new SearchClient((q: string) =>
		activeContactId === null ? searchUrl(q) : `${searchUrl(q)}&contact_id=${activeContactId}`
	);
</script>

<!-- Round header trigger — opens the palette. The header's shared glass round. -->
<button
	type="button"
	onclick={() => (open = true)}
	aria-label="Search"
	class={CLIENT_HEADER_BUTTON_CLASS}
>
	<SearchIcon class={CLIENT_HEADER_ICON_CLASS} />
</button>

<SpotlightSearch
	bind:open
	scopes={SEARCH_SCOPES}
	icons={ICONS}
	typeLabels={TYPE_LABELS}
	routes={ROUTES}
	{client}
	onnavigate={(href) => void goto(href)}
	title="Search"
	description="Search your accounts, holdings, transactions, documents and insights"
	placeholder="Search your financial world…"
	ariaLabel="Search accounts, holdings, transactions, documents and insights"
/>
