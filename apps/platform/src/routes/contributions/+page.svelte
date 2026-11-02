<script lang="ts">
	// The contributions workbench — a thin mount, per the portal waterfall.
	//
	// Everything visual lives in `@graphene/ui/book/contributions`; this page owns the loader, the
	// form actions and the toast. That split is what lets the admin portal render the same
	// workbench read-only over a firm's book (`actions` absent ⇒ no controls).
	//
	// Mutations go through form actions rather than fetch: they get CSRF, progressive enhancement
	// and a server-side re-read for free, and `update()` refreshes the loaded rows so the pipeline
	// counts and the unclassified queue both move the moment something is saved.
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Streamed, toast } from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import {
		ContributionsSkeleton,
		ContributionsWorkbench,
		type ContributionLinks
	} from '@graphene/ui/book/contributions';
	import type { ActionResult } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let busy = $state(false);

	/** The hidden form each action submits through — one form, retargeted per action. A panel is
	 *  a control surface, not a set of nested forms, and nesting a form per button inside a grid
	 *  cell breaks the layout. */
	let formEl = $state<HTMLFormElement | null>(null);
	let action = $state('?/classify');
	let payload = $state<Record<string, string>>({});

	function errorOf(result: ActionResult): string {
		return result.type === 'failure'
			? String(result.data?.message ?? 'Something went wrong.')
			: 'Something went wrong.';
	}

	function submit(actionName: string, fields: Record<string, unknown> = {}) {
		action = actionName;
		payload = Object.fromEntries(
			Object.entries(fields)
				.filter(([, v]) => v !== undefined && v !== null)
				.map(([k, v]) => [k, String(v)])
		);
		// Let the bindings flush before the browser reads the form's inputs.
		queueMicrotask(() => formEl?.requestSubmit());
	}

	/** Injected navigation, per the portal waterfall. The workbench hardcodes no hrefs; this names
	 *  the platform's own client and account routes, addressed by uid like every other record URL
	 *  in the estate (`docs/api.md` → "Addressing") and spelled the same way `/accounts` builds its
	 *  own `AccountBookLinks`. */
	const links: ContributionLinks = {
		client: (uid: string) => `/clients/${uid}`,
		account: (uid: string) => `/accounts/${uid}`
	};

	/** Real links for the pager. The current params are passed so an unrelated one survives. */
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	/** The workbench is server-driven, so a requested query becomes a URL. `?focus=` survives
	 *  because `paramsFromQuery` starts from the current params. */
	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	// The panel sends the classification and nothing else — the contributor block is gone (the
	// employer is the client's own fact, read server-side from their record) and the frequency and
	// tax year are derived or left standing. Both actions RECORD to Morgan Lloyd in the same call;
	// see `+page.server.ts`, which is where the chaining lives.
	//
	// There is no `runMatcher`: `POST /contributions/match` is still an action below, for ops and
	// for the scheduled matcher, but it is a reconciliation sweep rather than an adviser's act and
	// the workbench no longer offers a button for it.
	const actions = {
		classify: (uid: string, patch: Record<string, unknown>) =>
			submit('?/classify', { uid, category: patch.category, paid_by: patch.paid_by }),
		lodge: (uid: string) => submit('?/lodge', { uid }),
		cancel: (uid: string, reason: string) => submit('?/cancel', { uid, reason }),
		refreshFromMorganLloyd: (uid: string) => submit('?/refresh', { uid }),
		// A detected credit takes the same patch as a classification — the panel is one form —
		// but creates the contribution rather than amending one.
		classifyCredit: (transactionId: number, patch: Record<string, unknown>) =>
			submit('?/classifyCredit', {
				transaction_id: transactionId,
				category: patch.category,
				paid_by: patch.paid_by
			})
	};
</script>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ContributionsSkeleton />
	{/snippet}
	{#snippet children(book)}
		<ContributionsWorkbench
			contributions={book.contributions}
			credits={book.credits}
			summary={book.summary}
			focus={data.focus}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			onquery={navigate}
			pageHref={href}
			{links}
			{busy}
			{actions}
		/>
	{/snippet}
</Streamed>

<form
	bind:this={formEl}
	method="POST"
	{action}
	class="hidden"
	use:enhance={() => {
		busy = true;
		return async ({ result, update }) => {
			busy = false;
			if (result.type === 'success') {
				// A classification that saved but could not reach Morgan Lloyd is a SUCCESS with a
				// warning, not a failure: the record is ours and it is written. The row stays in
				// "Not lodged", which is what that metric is for.
				const warning = result.data?.warning;
				if (warning) toast.warning(String(warning));
				else toast.success(String(result.data?.message ?? 'Saved.'));
			} else {
				toast.error(errorOf(result));
			}
			await update({ reset: false });
		};
	}}
>
	{#each Object.entries(payload) as [name, value] (name)}
		<input type="hidden" {name} {value} />
	{/each}
</form>
