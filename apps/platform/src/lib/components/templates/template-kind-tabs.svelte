<script lang="ts" module>
	/** Every view of `/templates`, as it appears in the `?kind=` query. */
	export type TemplateKind = 'documents' | 'emails';
</script>

<script lang="ts">
	/**
	 * The view switch on `/templates`: ONE row over the two kinds of template.
	 *
	 *   Documents | Emails
	 *
	 * Datasets are not on it: a dataset is an input to a document template; its door is in the kebab.
	 *
	 * SWITCHING KIND RE-RUNS THE LOADER: the kind lives in `?kind=`, set with `replaceState`.
	 *
	 * An unreadable view is not offered, and a single remaining view renders NOTHING.
	 */
	import { Segmented } from '@graphene/ui';
	import { goto } from '$app/navigation';

	let {
		kind,
		canDocuments,
		canEmails
	}: {
		/** The view on screen, straight from `?kind=`. */
		kind: TemplateKind;
		canDocuments: boolean;
		canEmails: boolean;
	} = $props();

	const options = $derived(
		[
			canDocuments ? { value: 'documents', label: 'Documents' } : null,
			canEmails ? { value: 'emails', label: 'Emails' } : null
		].filter((o) => o !== null)
	);

	const showSwitch = $derived(options.length > 1);

	function show(next: string): void {
		if (next === kind) return;
		// Documents is the default view, so it is the ABSENCE of the param rather than
		// `?kind=documents` — one canonical URL per view, and `/templates` keeps meaning what it has
		// always meant.
		void goto(next === 'documents' ? '?' : `?kind=${next}`, {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	}
</script>

{#if showSwitch}
	<Segmented {options} value={kind} label="Template kind" onValueChange={show} />
{/if}
