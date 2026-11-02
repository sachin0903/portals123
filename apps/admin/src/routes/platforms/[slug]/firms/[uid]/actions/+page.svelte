<script lang="ts">
	// The write actions, as side-drawer forms built from the vendor's own field contract.
	//
	// Every input here is generated from `data.actions[].fields` — name, type, requiredness and
	// option set all came from parsing Morgan Lloyd's documentation. Nothing about the shape of
	// these forms is written twice, so a field the vendor adds appears the next time the manifest
	// is rebuilt, and one they remove stops being offered.
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		Checkbox,
		Input,
		Label,
		MoneyInput,
		RecordPicker,
		SectionHeading,
		Select,
		Sheet
	} from '@graphene/ui';
	import type { RecordOption } from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import type { ActionField, PlatformAction } from './+page.server';

	let { data, form } = $props();

	let open = $state(false);
	let active = $state<PlatformAction | null>(null);

	let entityId = $state('');
	// Money fields bind a number in MAJOR units (MoneyInput's contract), so they are held apart
	// from the string-valued rest and serialised into a hidden input on submit.
	let money = $state<Record<string, number | null>>({});
	/** Option-set selections, likewise bound rather than posted directly. */
	let choices = $state<Record<string, string>>({});
	/** Multiselect members, posted as the one comma-separated string the vendor parses. */
	let picked = $state<Record<string, number[]>>({});
	let submitting = $state(false);

	const groups = $derived(
		[...new Set(data.actions.map((a) => a.group))].map((group) => ({
			group,
			actions: data.actions.filter((a) => a.group === group)
		}))
	);

	// The outcome is shown on the PAGE, not only inside the drawer. Submitting can end in a
	// navigation that closes the sheet, and the one thing that must never be lost is what the
	// vendor said — a refusal naming a field, or the identifier a create just minted.
	const outcome = $derived(form ?? null);
	const outcomeLabel = $derived(
		data.actions.find((a) => a.key === outcome?.key)?.label ?? outcome?.key ?? ''
	);
	// Inside the drawer, only when it belongs to the action on screen.
	const result = $derived(form && active && form.key === active.key ? form : null);

	function openAction(action: PlatformAction) {
		active = action;
		entityId = '';
		// SEEDED, not left empty. `MoneyInput` declares `value = $bindable(null)`, and Svelte
		// refuses `bind:value={undefined}` against a prop with a fallback — so an unseeded key
		// throws the moment the drawer renders a decimal field, taking the whole sheet with it.
		money = Object.fromEntries(
			action.fields.filter((f) => kind(f) === 'money').map((f) => [f.name, null])
		);
		// Both controls that bind a Select — the option sets and the True/False booleans. A field
		// with a configured default opens on it rather than on "Select…": `schemeType` and
		// `sippType` are option-set members the vendor validates as a PAIR, so picking them by
		// hand from a Word document is a transcription error the vendor reports as
		// "Invalid Scheme and Sipp Type combination" without saying which half is wrong.
		choices = Object.fromEntries(
			action.fields
				.filter((f) => kind(f) === 'select' || kind(f) === 'bool')
				.map((f) => [f.name, action.defaults?.[f.name] ?? ''])
		);
		picked = Object.fromEntries(
			action.fields.filter((f) => kind(f) === 'multi').map((f) => [f.name, []])
		);
		open = true;
	}

	function toggleMember(field: ActionField, value: number, on: boolean) {
		const current = picked[field.name] ?? [];
		picked[field.name] = on ? [...current, value] : current.filter((member) => member !== value);
	}

	/** What the trigger shows: the chosen option's label, or a prompt. */
	function choiceLabel(field: ActionField, value: string | undefined): string {
		if (!value) return 'Select…';
		const option = field.options.find((o) => String(o.value) === value);
		return option ? optionLabel(option) : value;
	}

	type Control =
		'lookup' | 'multi' | 'select' | 'money' | 'percent' | 'number' | 'date' | 'bool' | 'text';

	/** Search the firm's own records for one identifier field.
	 *
	 * Curried per field rather than one shared function, because `RecordPicker` knows nothing
	 * about routes — it takes a `search` and calls it. The endpoint resolves the FIELD name
	 * through the registry, so no relation name travels in a URL.
	 */
	function searchFor(field: ActionField) {
		return async (query: string): Promise<RecordOption[]> => {
			const url = new URL(
				`/platforms/${page.params.slug}/firms/${page.params.uid}/lookups/${field.name}`,
				location.origin
			);
			if (query) url.searchParams.set('q', query);
			const res = await fetch(url);
			if (!res.ok) throw new Error(`Could not search ${field.lookup} (${res.status}).`);
			return (await res.json()) as RecordOption[];
		};
	}

	/** A percentage is a `decimal` like any other, and a `£` in front of it is a lie.
	 *
	 * Three of `cam_createscheme`'s fields are fee RATES documented "must be between 0 and 100";
	 * MoneyInput would show `£ 0.35` and group thousands on a number that cannot exceed a hundred.
	 */
	const PERCENT = /percent/i;

	/** Which control a documented type deserves.
	 *
	 * Types are matched LOWER-CASED because the vendor's page is not consistent about it: `int`
	 * and `Int`, `bool` and `Bool` both appear, on different operations. Comparing verbatim sent
	 * `cam_drawdownsetup`'s four `Int` fields to a free-text box while `cam_createcontact`'s `int`
	 * fields got a number one.
	 */
	function kind(field: ActionField): Control {
		const type = field.type.trim().toLowerCase();
		// A reference to one of the firm's own records beats every other rule: a GUID typed by
		// hand is the worst-behaved input on these forms, and the vendor answers a wrong one with
		// `0x80040217 does not exist`, which reads as an outage.
		if (field.lookup) return 'lookup';
		if (field.multiselect) return 'multi';
		if (field.options.length) return 'select';
		if (type === 'decimal')
			return PERCENT.test(field.name + field.description) ? 'percent' : 'money';
		if (type === 'int') return 'number';
		if (type === 'bool') return 'bool';
		// `datetime` deliberately stays text: a date input would drop a time the vendor may want.
		if (type === 'date') return 'date';
		return 'text';
	}

	function optionLabel(option: { label: string | null; value: number }): string {
		// `cam_taxrelief` documents its four values with no labels at all — their text lives in a
		// separate lookup table. The number alone is the honest label there.
		return option.label ? `${option.label} (${option.value})` : String(option.value);
	}

	const isPatch = $derived(active?.method === 'patch_entity');
</script>

<svelte:head><title>Actions · Platform data</title></svelte:head>

<div class="space-y-5">
	<!-- A band within the firm console, whose own header names the firm above it — so the shared
	     section band rather than a fourth hand-rolled type size. -->
	<div>
		<SectionHeading title="Actions" />
		<p class="text-muted-foreground text-sm">
			Sent to the live {data.provider.replace('_', ' ')} org on this firm's behalf. Every call is recorded
			in the <a href="/platforms/api" class="underline">API log</a>.
		</p>
	</div>

	{#if !data.canWrite}
		<Alert>
			<AlertDescription>
				You can see what each action requires, but cannot perform it. This needs
				<span class="font-mono text-xs">platforms:write</span>.
			</AlertDescription>
		</Alert>
	{/if}

	{#if outcome?.message}
		<Alert variant="destructive">
			<AlertDescription>
				<span class="font-medium">{outcomeLabel}:</span>
				{outcome.message}
			</AlertDescription>
		</Alert>
	{:else if outcome?.success}
		<Alert>
			<AlertDescription>
				<p class="font-medium">{outcomeLabel} sent.</p>
				<pre
					class="bg-muted/40 mt-2 max-h-52 overflow-auto rounded-md p-2 font-mono text-xs">{JSON.stringify(
						outcome.response,
						null,
						2
					)}</pre>
			</AlertDescription>
		</Alert>
	{/if}

	{#each groups as { group, actions } (group)}
		<div>
			<p class="text-muted-foreground mb-2 text-xs font-medium">
				{group}
			</p>
			<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
				{#each actions as action (action.key)}
					<Card>
						<CardContent class="flex h-full flex-col gap-2">
							<div class="flex items-start justify-between gap-2">
								<p class="font-medium">{action.label}</p>
								{#if action.danger}
									<Badge variant="destructive" class="shrink-0 text-[10px]">Writes</Badge>
								{/if}
							</div>
							<p class="text-muted-foreground font-mono text-[11px]">{action.action}</p>
							{#if action.note}
								<p class="text-muted-foreground text-xs">{action.note}</p>
							{/if}
							<div class="flex-1"></div>
							<div class="flex items-center justify-between gap-2">
								<span class="text-muted-foreground text-xs">
									{action.fields.filter((f) => f.required).length} required ·
									{action.fields.length} fields
								</span>
								<Button size="sm" variant="outline" onclick={() => openAction(action)}>
									{data.canWrite ? 'Open' : 'View'}
								</Button>
							</div>
						</CardContent>
					</Card>
				{/each}
			</div>
		</div>
	{/each}
</div>

<Sheet.Root bind:open>
	<Sheet.Content class="flex w-full flex-col gap-0 overflow-hidden p-0">
		{#if active}
			<Sheet.Header class="shrink-0 border-b px-6 pt-6 pb-4">
				<Sheet.Title>{active.label}</Sheet.Title>
				<Sheet.Description class="font-mono text-xs">
					{active.method === 'patch_entity' ? 'PATCH' : 'POST'}
					{active.action}
				</Sheet.Description>
			</Sheet.Header>

			<form
				method="POST"
				action="?/run"
				use:enhance={() => {
					submitting = true;
					return async ({ update }) => {
						// `reset: false` keeps what was typed, so a refusal that names one bad field
						// does not cost the operator the other thirty-three.
						await update({ reset: false });
						submitting = false;
					};
				}}
				class="flex min-h-0 flex-1 flex-col"
			>
				<input type="hidden" name="__action" value={active.key} />

				<div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
					{#if active.danger}
						<!-- Before the form, not after it: Morgan Lloyd has no delete, so the moment to
						     say this is while the operator is deciding whether to fill it in. -->
						<Alert variant="destructive">
							<AlertDescription>{active.danger}</AlertDescription>
						</Alert>
					{/if}
					{#if active.retry === 'manual'}
						<!-- WHAT HAPPENS IF THIS IS SENT TWICE. Stated where the operator decides, which
						     is the only place it is any use: if a submit fails ambiguously — a timeout,
						     a lost answer — there is no way to ask the vendor whether it landed, and
						     Morgan Lloyd has no delete. Resending a create puts a second human in the
						     CRM; resending a contribution reclaims relief from HMRC twice. -->
						<Alert>
							<AlertDescription>
								This cannot be safely repeated. If the submit fails without a clear answer, confirm
								at the provider before sending it again.
							</AlertDescription>
						</Alert>
					{/if}
					{#if active.note}
						<p class="text-muted-foreground text-xs">{active.note}</p>
					{/if}

					{#if result?.message}
						<Alert variant="destructive">
							<AlertDescription>{result.message}</AlertDescription>
						</Alert>
					{:else if result?.success}
						<Alert>
							<AlertDescription>
								<p class="font-medium">Sent.</p>
								<pre
									class="bg-muted/40 mt-2 max-h-52 overflow-auto rounded-md p-2 font-mono text-xs">{JSON.stringify(
										result.response,
										null,
										2
									)}</pre>
							</AlertDescription>
						</Alert>
					{/if}

					{#if isPatch}
						<div class="space-y-1.5">
							<Label for="__entity_id">Record id <span class="text-destructive">*</span></Label>
							<Input
								id="__entity_id"
								name="__entity_id"
								bind:value={entityId}
								placeholder="The vendor GUID this updates"
								disabled={!data.canWrite}
							/>
							<p class="text-muted-foreground text-xs">
								<!-- The vendor's own words where it has them ("The GUID of the Personal Asset to
								     update"), which name the ENTITY rather than saying "a record". -->
								{active.parameters?.[0]?.description ||
									'An entity PATCH targets one existing record by its Dynamics GUID.'}
							</p>
						</div>
					{/if}

					{#each active.fields as field (field.name)}
						{@const control = kind(field)}
						<div class="space-y-1.5">
							<Label for={field.name} class="font-mono text-xs">
								{field.name}
								{#if field.required}<span class="text-destructive">*</span>{/if}
								{#if field.conditional}
									<Badge variant="ghost" class="ml-1 text-[10px]">
										{field.required_note}
									</Badge>
								{/if}
							</Label>

							{#if control === 'lookup'}
								<!-- `RecordPicker`, not `Combobox`/`MultiSelect`: those portal their panel to
							     document.body, outside this Sheet's focus trap, where it never receives a
							     click. The picker posts the identifier through its own hidden input. -->
								<RecordPicker
									id={field.name}
									name={field.name}
									search={searchFor(field)}
									placeholder={`Search ${field.lookup.toLowerCase()}…`}
									emptyText={`No ${field.lookup.toLowerCase()} match.`}
									capNote="First 50 matches. Type to narrow."
									disabled={!data.canWrite}
								/>
							{:else if control === 'multi'}
								<!-- CHECKBOXES, not `MultiSelect`. That component is Popover+Command based, and
								     `Popover.Content` portals to `document.body` — outside the Sheet's focus
								     trap, where it never receives a click. The vendor's own shape is a
								     comma-separated string of members, built here from what is ticked. -->
								<div
									class="max-h-56 space-y-2 overflow-y-auto rounded-md border p-3"
									role="group"
									aria-label={field.name}
								>
									{#each field.options as option (option.value)}
										{@const on = (picked[field.name] ?? []).includes(option.value)}
										<div class="flex items-start gap-2">
											<Checkbox
												id={`${field.name}-${option.value}`}
												checked={on}
												disabled={!data.canWrite}
												class="mt-0.5"
												onCheckedChange={(next) => toggleMember(field, option.value, next)}
											/>
											<Label
												for={`${field.name}-${option.value}`}
												class="text-xs leading-snug font-normal"
											>
												{optionLabel(option)}
											</Label>
										</div>
									{/each}
								</div>
								<input
									type="hidden"
									name={field.name}
									value={(picked[field.name] ?? []).join(',')}
								/>
							{:else if control === 'bool'}
								<Select.Root type="single" bind:value={choices[field.name]}>
									<Select.Trigger id={field.name} class="w-full" disabled={!data.canWrite}>
										{choices[field.name] || 'Select…'}
									</Select.Trigger>
									<Select.Content>
										<Select.Item value="True" label="True" />
										<Select.Item value="False" label="False" />
									</Select.Content>
								</Select.Root>
								<input type="hidden" name={field.name} value={choices[field.name] ?? ''} />
							{:else if control === 'select'}
								<!-- Select.Root binds a value rather than posting one, so the hidden input
								     is what reaches the form — the same arrangement MoneyInput needs. -->
								<Select.Root type="single" bind:value={choices[field.name]}>
									<Select.Trigger id={field.name} class="w-full" disabled={!data.canWrite}>
										{choiceLabel(field, choices[field.name])}
									</Select.Trigger>
									<Select.Content>
										{#each field.options as option (option.value)}
											<Select.Item value={String(option.value)} label={optionLabel(option)} />
										{/each}
									</Select.Content>
								</Select.Root>
								<input type="hidden" name={field.name} value={choices[field.name] ?? ''} />
							{:else if control === 'money'}
								<!-- `value` + `onchange`, NOT `bind:`. MoneyInput declares
								     `value = $bindable(null)`, and binding into a record entry that Svelte
								     evaluates as `undefined` at any point throws `props_invalid_value` and
								     takes the whole drawer down. A one-way value with a change callback
								     cannot reach that state. The hidden input is what actually posts. -->
								<MoneyInput
									value={money[field.name] ?? null}
									disabled={!data.canWrite}
									onchange={(next) => (money[field.name] = next)}
								/>
								<input type="hidden" name={field.name} value={money[field.name] ?? ''} />
							{:else if control === 'percent'}
								<!-- A rate, not an amount: no currency symbol, and bounded the way the vendor's
								     own note bounds it ("must be between 0 and 100"). -->
								<div class="relative">
									<Input
										id={field.name}
										name={field.name}
										type="number"
										step="0.01"
										min="0"
										max="100"
										class="pr-7"
										disabled={!data.canWrite}
									/>
									<span
										class="text-muted-foreground pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs"
									>
										%
									</span>
								</div>
							{:else}
								<Input
									id={field.name}
									name={field.name}
									type={control === 'number' ? 'number' : control === 'date' ? 'date' : 'text'}
									value={active.defaults?.[field.name] ?? ''}
									disabled={!data.canWrite}
								/>
							{/if}

							{#if active.defaults?.[field.name]}
								<!-- Said out loud. A prefilled GUID an operator did not type is otherwise
								     indistinguishable from one the page invented. -->
								<p class="text-muted-foreground text-xs">
									From the platform's Morgan Lloyd configuration.
								</p>
							{:else if field.description && !field.options.length}
								<p class="text-muted-foreground text-xs">{field.description}</p>
							{/if}
						</div>
					{/each}

					{#if active.lookups?.length}
						<div class="rounded-md border p-3">
							<p class="mb-2 text-xs font-medium">Lookup</p>
							<dl class="space-y-1">
								{#each Object.entries(active.lookups[0]) as [key, value] (key)}
									<div class="text-xs">
										<dt class="text-muted-foreground inline font-mono">{key}</dt>
										<dd class="inline">: {value}</dd>
									</div>
								{/each}
							</dl>
						</div>
					{/if}
				</div>

				<div class="flex shrink-0 justify-end gap-2 border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (open = false)}>Close</Button>
					{#if data.canWrite}
						<Button type="submit" disabled={submitting}>
							{submitting ? 'Sending…' : 'Send to ' + data.provider.replace('_', ' ')}
						</Button>
					{/if}
				</div>
			</form>
		{/if}
	</Sheet.Content>
</Sheet.Root>
