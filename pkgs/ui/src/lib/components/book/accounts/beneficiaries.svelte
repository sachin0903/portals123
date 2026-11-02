<script lang="ts">
	// WHO THE PENSION PAYS OUT TO, and in what share.
	//
	// A pension's death benefits sit outside the estate, so they are not directed by a will: the
	// member's expression of wish is the only instruction the scheme administrator has. That makes
	// this a record with an unusual property — it is read once, years later, by somebody who cannot
	// ask the member what they meant. So the panel is built around being CHECKABLE at a glance:
	// largest share first, a running total stated in words, and the shortfall named rather than
	// implied.
	//
	// ADD AND REMOVE, NO EDIT. A nomination is replaced by withdrawing it and making a new one,
	// which is what an expression-of-wish form actually is — the member signs a fresh one. An
	// edit-in-place would let a share move under a name with the audit row saying only "updated".
	import PlusIcon from '@lucide/svelte/icons/plus';
	import TrashIcon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { InputGroup, InputGroupAddon, InputGroupInput } from '../../ui/input-group/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { cn } from '../../../utils.js';
	import { PERSON_TITLE_ITEMS } from '../clients/types.js';
	import type { Beneficiary } from './types.js';

	let {
		beneficiaries = [],
		enhance,
		addAction,
		removeAction
	}: {
		beneficiaries?: Beneficiary[];
		/** The app's own `enhance`, per the portal waterfall. Absent (with the actions) ⇒ the panel
		 *  is a read-only list, which is how the admin portal mounts it. */
		enhance?: (node: HTMLFormElement) => { destroy(): void };
		addAction?: string;
		removeAction?: string;
	} = $props();

	const writable = $derived(!!enhance && !!addAction);
	// `use:` needs a plain identifier and cannot take `enhance?.` — aliased with a noop fallback
	// that is never reached, because every form is inside a `{#if writable}` branch. The same
	// arrangement the client record uses for its injected enhance.
	const enhanceForm = $derived(enhance ?? (() => ({ destroy() {} })));
	let adding = $state(false);

	const allocated = $derived(
		beneficiaries.reduce((sum, b) => sum + (Number(b.proportion) || 0), 0)
	);
	const remaining = $derived(Math.round((100 - allocated) * 1000) / 1000);
	/** A hundredth of a percent of slack. Three 33.333% nominations total 99.999 — a complete set
	 *  entered as precisely as `numeric(6,3)` allows — and reporting that as a 0.001% shortfall
	 *  would send a firm looking for a fourth beneficiary that does not exist. The tolerance is
	 *  smaller than any share worth nominating and larger than any rounding the column can produce. */
	const complete = $derived(remaining < 0.01);
	const fmt = (n: number) => `${Number(n.toFixed(3))}%`;
	/** The title and relationship pickers' own values — `SelectMenu` binds rather than
	 *  reading a DOM default, and posts each through the hidden input its `name` mints.
	 *  Title is `PERSON_TITLE_ITEMS`, the `title` field type's list — the same six the
	 *  client record and the onboarding applicant form offer. */
	let title = $state('');
	let relationship = $state('');

	const relationships = [
		'Spouse',
		'Civil partner',
		'Partner',
		'Child',
		'Grandchild',
		'Parent',
		'Sibling',
		'Trust',
		'Charity',
		'Other'
	];
</script>

<Card glass class="gap-2">
	<CardHeader class="flex flex-row items-center justify-between gap-3">
		<div class="min-w-0">
			<CardTitle class="text-base">Beneficiaries</CardTitle>
			<p class="text-muted-foreground mt-0.5 text-xs">
				Who this pension pays out to. Death benefits sit outside the estate, so a will does not
				direct them.
			</p>
		</div>
		{#if writable && !adding}
			<Button variant="outline" size="sm" onclick={() => (adding = true)}>
				<PlusIcon class="mr-1.5 size-4" />
				Add
			</Button>
		{/if}
	</CardHeader>
	<CardContent class="space-y-3">
		{#if beneficiaries.length === 0}
			<p class="text-muted-foreground text-sm">
				No beneficiaries nominated. Without one the scheme administrator decides at their own
				discretion.
			</p>
		{:else}
			<ul class="divide-border/60 divide-y">
				{#each beneficiaries as b (b.uid)}
					<li class="flex items-center justify-between gap-3 py-2">
						<div class="min-w-0">
							<p class="truncate text-xs font-medium">
								{[b.title, b.name].filter(Boolean).join(' ')}
							</p>
							<p class="text-muted-foreground text-xs">
								{b.relationship ?? 'Relationship not stated'}
							</p>
						</div>
						<div class="flex shrink-0 items-center gap-2">
							<span class="text-xs font-medium tabular-nums">{fmt(Number(b.proportion))}</span>
							{#if writable && removeAction}
								<form method="POST" action={removeAction} use:enhanceForm>
									<input type="hidden" name="uid" value={b.uid} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										title="Withdraw this nomination"
										aria-label="Withdraw {b.name}'s nomination"
									>
										<TrashIcon class="size-4" />
									</Button>
								</form>
							{/if}
						</div>
					</li>
				{/each}
			</ul>

			<!-- THE RUNNING TOTAL, in words. A set that does not reach 100% is not refused by the API —
			     it is built a row at a time, so demanding 100 would make the first insert fail — which
			     means the only thing that can tell a firm they are not finished is this line. -->
			<!-- COLOURED ON `complete`, not on exact equality. `remaining === 0` ignored the
			     tolerance the words honour, so three 33.333% nominations — a complete set entered
			     as precisely as `numeric(6,3)` allows — read "Fully allocated" in warning amber:
			     the "go and find a fourth beneficiary" outcome the tolerance exists to suppress,
			     moved out of the words and into the colour. -->
			<p class={cn('text-xs', complete ? 'text-success-strong' : 'text-warning-strong')}>
				{#if complete}
					Fully allocated — {fmt(allocated)} of the fund.
				{:else}
					{fmt(allocated)} allocated. <strong>{fmt(remaining)} is unallocated</strong> and would fall
					to the administrator's discretion.
				{/if}
			</p>
		{/if}

		{#if writable && adding && addAction}
			<form
				method="POST"
				action={addAction}
				use:enhanceForm
				class="border-border/70 grid gap-3 rounded-lg border p-3 sm:grid-cols-2"
			>
				<div class="space-y-1">
					<Label for="ben-title">Title</Label>
					<SelectMenu
						id="ben-title"
						name="title"
						items={PERSON_TITLE_ITEMS}
						bind:value={title}
						placeholder="Select title"
					/>
				</div>
				<div class="space-y-1">
					<Label for="ben-name">Name</Label>
					<Input id="ben-name" name="name" required placeholder="Full name" />
				</div>
				<div class="space-y-1">
					<Label for="ben-relationship">Relationship</Label>
					<!-- `SelectMenu`, never a native `<select>`: docs/design-language.md names that
					     element in the Never column and records why the component exists ("76
					     native selects each carried a copy-pasted style string"). It renders a
					     hidden input when given `name`, so the `use:enhance` POST below is
					     unchanged. -->
					<SelectMenu
						id="ben-relationship"
						name="relationship"
						items={[
							{ value: '', label: '—' },
							...relationships.map((r) => ({ value: r, label: r }))
						]}
						bind:value={relationship}
					/>
				</div>
				<div class="space-y-1">
					<Label for="ben-proportion">Share of the fund</Label>
					<!-- A PERCENTAGE, not money: `cam_percentageoffund` is what the vendor takes, and a
					     share stays right when the fund's value changes where an amount does not.
					     `step="0.001"` because the column is numeric(6,3) — a three-way split has to be
					     enterable as 33.333, or whole numbers make a complete set impossible. `max` is
					     the headroom rather than 100: the API refuses an over-allocation with a 409, and
					     a control that lets you type past it is a control that invites the refusal. -->
					<InputGroup>
						<InputGroupInput
							id="ben-proportion"
							name="proportion"
							type="number"
							step="0.001"
							min="0.001"
							max={remaining}
							required
						/>
						<InputGroupAddon align="inline-end">%</InputGroupAddon>
					</InputGroup>
					<!-- `!complete` for the same reason: the share box reported "0.001% unallocated"
					     beneath a line saying the set was full. -->
					{#if !complete}
						<p class="text-muted-foreground text-xs">{fmt(remaining)} unallocated</p>
					{/if}
				</div>
				<div class="flex items-center justify-end gap-2 sm:col-span-2">
					<Button type="button" variant="ghost" onclick={() => (adding = false)}>Cancel</Button>
					<Button type="submit">Nominate</Button>
				</div>
			</form>
		{/if}
	</CardContent>
</Card>
