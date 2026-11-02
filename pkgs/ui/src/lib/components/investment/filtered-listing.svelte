<script lang="ts" generics="T">
	import type { Component, Snippet } from 'svelte';
	import WidgetToolbar from './widget-toolbar.svelte';
	import {
		ALL,
		accountItems,
		categoryCounts,
		filterListing,
		type ListingAccessors,
		type ListingQuery
	} from './listing.js';

	// The stateful shell around one listing widget: owns the toolbar's search/account/category
	// state and hands the CALLER back the matched rows plus the toolbar as a snippet, so each
	// look places the controls where its chrome wants them (inside the card content, above a
	// chrome-less register). One instance per block ⇒ two listing blocks on a page filter
	// independently.
	//
	// Two modes:
	//  - uncontrolled (no `onquerychange`): state lives here and the slice is filtered
	//    client-side — the studio preview and any complete slice. If the slice is PARTIAL
	//    (`partial`, e.g. one page of a paginated book) the controls disable with an explanation
	//    instead: searching a fraction of the data is a silently wrong answer, the same lie the
	//    DataTable sort-disable exists to prevent.
	//  - controlled (`onquerychange` provided): the app owns q/account (PortalPage's
	//    `listingQuery`, URL-driven next wave), changes are reported upwards and NO client-side
	//    q/account filtering happens — the server already answered the query. Category pills
	//    (documents) stay client-side either way: the slice is complete and the server knows
	//    nothing of display categories.
	let {
		rows,
		toolbar = false,
		searchText,
		accountOf = null,
		accountOptions = null,
		categoryOf = null,
		categoryStyle = 'pills',
		categoryIcon = null,
		note = null,
		searchScope = null,
		partial = false,
		query = null,
		onquerychange = null,
		placeholder,
		label,
		interactive = true,
		children
	}: {
		/** The block's (already capped-at-source) data slice, unfiltered. */
		rows: T[];
		/** The block's `toolbar` setting; off renders nothing but the pass-through rows. */
		toolbar?: boolean;
		searchText: ListingAccessors<T>['searchText'];
		accountOf?: ListingAccessors<T>['accountOf'];
		/**
		 * The client's WHOLE account list, for the account filter's options.
		 *
		 * Required in CONTROLLED mode and meaningless outside it. The server has already cut
		 * `rows` down to the chosen account, so options derived from them collapse to that one
		 * account and the control hides itself — leaving a filter in force that nothing on the
		 * page can clear or change. Deriving from the rows is right only where the rows ARE the
		 * whole slice, which is the uncontrolled case.
		 */
		accountOptions?: (string | null | undefined)[] | null;
		categoryOf?: ListingAccessors<T>['categoryOf'];
		/** Pills under the header, a grid of category tiles, or the browse bar's tabs (WidgetToolbar). */
		categoryStyle?: 'pills' | 'tiles' | 'tabs';
		categoryIcon?: ((value: string) => Component) | null;
		/** Right-aligned toolbar note (the documents unread count). */
		note?: string | null;
		/** The palette category the `tabs` style's search button opens on (WidgetToolbar). */
		searchScope?: string | null;
		/** True when `rows` is one page of a bigger set (its `*_total` outruns it). */
		partial?: boolean;
		/** Externally-owned filter state (controlled mode) — see PortalPage.listingQuery. */
		query?: ListingQuery | null;
		onquerychange?: ((next: ListingQuery) => void) | null;
		placeholder?: string;
		label: string;
		interactive?: boolean;
		/** The look's body, given matched rows, its toolbar and whether any filter is active. */
		children: Snippet<[T[], Snippet, boolean]>;
	} = $props();

	let localQ = $state('');
	let localAccount = $state(ALL);
	let category = $state(ALL);

	const controlled = $derived(onquerychange !== null);
	const q = $derived(controlled ? (query?.q ?? '') : localQ);
	const account = $derived(controlled ? (query?.account ?? ALL) : localAccount);
	// Partial slice + nobody to run the query for real ⇒ the controls must not pretend.
	const locked = $derived(partial && !controlled);

	const accounts = $derived.by(() => {
		const of = accountOf; // narrow once — the closure below must not re-read a `| null` prop
		if (!of) return null; // a listing with no account axis (documents) never offers the filter
		// The supplied list wins wherever there is one; the rows are the fallback, not the rule.
		return accountItems(accountOptions ?? rows.map((r) => of(r)), account);
	});
	const categories = $derived.by(() => {
		const of = categoryOf;
		return of ? categoryCounts(rows.map((r) => of(r))) : null;
	});

	const matched = $derived(
		filterListing(
			rows,
			{
				// Controlled q/account already shaped the slice server-side; locked controls
				// must not half-apply a filter the user can't complete.
				q: controlled || locked ? '' : q,
				account: controlled || locked ? ALL : account,
				category
			},
			{ searchText, accountOf, categoryOf }
		)
	);

	function setQ(value: string) {
		if (controlled) onquerychange?.({ ...(query ?? {}), q: value.trim() || undefined });
		else localQ = value;
	}
	/** Is a filter actually in force? Both halves of the state, since either mode can hold one. */
	const filtered = $derived(q.trim() !== '' || account !== ALL || category !== ALL);

	/**
	 * A search box earns its row only once the list outgrows a glance. Eight rows is roughly a
	 * phone screen of them: below that a client scans faster than they type, and the box reads as
	 * a tool for a library they do not have (the documents page with three statements). A search
	 * in force or a partial (paginated) slice keeps it whatever the count — the first because
	 * the box is how the search is cleared, the second because the rows on screen are not the
	 * list. A category or account filter does NOT summon it: those have their own controls, and
	 * a box appearing on the tap of a tile is a layout jump for nothing.
	 */
	const SEARCH_MIN_ROWS = 8;
	const showSearch = $derived(q.trim() !== '' || partial || rows.length > SEARCH_MIN_ROWS);
	const showAccounts = $derived(accounts !== null && (accounts.length > 2 || account !== ALL));
	const showPills = $derived(categories !== null && categories.length > 1);

	function setAccount(value: string) {
		if (controlled)
			onquerychange?.({ ...(query ?? {}), account: value === ALL ? undefined : value });
		else localAccount = value;
	}
</script>

{#snippet controls()}
	<!-- Nothing to search ⇒ no toolbar: the block's empty state stands alone. But a filter IN
	     FORCE keeps the controls whatever the row count, because zero rows is the one case where
	     a reader most needs them: under a server-side filter an account with no matches empties
	     `rows`, and hiding the toolbar on that would strand the reader on an empty listing with
	     no way to clear the filter that emptied it — and no way to tell that from a client who
	     owns nothing. -->
	{#if toolbar && (rows.length > 0 || filtered) && (showSearch || showAccounts || showPills)}
		<WidgetToolbar
			search={showSearch}
			query={q}
			onquery={setQ}
			{accounts}
			{account}
			onaccount={setAccount}
			{categories}
			{category}
			{categoryStyle}
			{categoryIcon}
			oncategory={(v) => (category = v)}
			{note}
			{searchScope}
			searchDisabled={locked}
			{placeholder}
			{label}
			{interactive}
		/>
	{/if}
{/snippet}

{@render children(matched, controls, filtered)}
