<!--
	“Starts when…” — the automations whose target is THIS journey.

	A flow does not start itself. Phase A made starting one a rule like any other: a row in
	`flows.automations` with a `flow_id`, ordered and armed alongside every other rule the firm has.
	This panel is that slice of the rule tier, shown where an author is actually thinking about it —
	on the journey — rather than on /automations, where "what starts this?" was previously
	unanswerable without reading every rule in the firm.

	**Self-contained on purpose.** It takes three props and loads its own data through
	`/flows/triggers`, a same-origin endpoint in this app. The alternative — threading four form
	actions through the builder's `+page.server.ts` — would couple the flow page to the rule tier for
	no benefit, and the panel still would not be mountable anywhere else.

	It deliberately does NOT mount `AutomationSheet`. The sheet posts to a host page's form action
	and carries the whole rule vocabulary (targets, schedules, re-arm windows, the self-trigger
	switch); here the target is already known and the only question is "when". What it shares with
	the sheet is what matters — the same trigger registry, the same `ConditionBuilder`, the same
	summaries — so the two cannot describe a rule differently.

	`automations:write`, not `flows:write`: arming the thing that starts a journey for real clients
	is a different grant from drawing the journey (flow-contract.json → `$comment_permissions`).
	`canManage` (the flow's own gate) only hides affordances; the endpoint is the control.
-->
<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		Input,
		Label,
		SelectMenu,
		StatusBadge,
		Switch,
		toast
	} from '@graphene/ui';
	import {
		CAPS,
		ConditionBuilder,
		isSupported,
		summariseConditions,
		toJson,
		toModel,
		triggerGroups,
		TRIGGER_BY_KEY,
		type AutomationRow,
		type BuilderGroup
	} from '@graphene/ui/automations';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';

	let {
		flowUid,
		flowName,
		canManage,
		endpoint = '/flows/triggers'
	}: { flowUid: string; flowName: string; canManage: boolean; endpoint?: string } = $props();

	const MAX_TRIGGERS = CAPS.maxTriggersPerFlow ?? 4;
	let rows = $state<AutomationRow[]>([]);
	let canWrite = $state(false);
	let loading = $state(true);
	let loadFailed = $state(false);

	let busy = $state(false);
	/** False when this user may not see triggers at all — the panel then renders NOTHING rather than
	 *  an error or an empty state. `automations:read` postdates every existing role, so "not yours"
	 *  is the common case on a firm's first look at this page, and it is not a fault to report. */
	let permitted = $state(true);

	// Keyed on the uid so the panel follows the page if the builder ever swaps flows in place.
	let loadedFor = $state('');
	$effect(() => {
		if (loadedFor === flowUid) return;
		loadedFor = flowUid;
		void reload();
	});

	async function reload(): Promise<void> {
		loading = true;
		try {
			const res = await fetch(`${endpoint}?flow=${encodeURIComponent(flowUid)}`);
			if (!res.ok) throw new Error(String(res.status));
			const body = await res.json();
			rows = body.automations ?? [];
			canWrite = !!body.canWrite;
			// Absent ⇒ permitted, so an older deployment of the endpoint keeps working.
			permitted = body.permitted !== false;
			loadFailed = false;
		} catch {
			// A read-only posture on failure, never a fabricated empty list: "nothing starts this
			// journey" and "we could not find out" are different answers, and only one is safe to show.
			loadFailed = true;
			rows = [];
			canWrite = false;
		} finally {
			loading = false;
		}
	}

	/** Pull the message out of a SvelteKit error response. */
	async function message(res: Response, fallback: string): Promise<string> {
		try {
			const body = await res.json();
			return String(body?.message ?? body?.error?.message ?? fallback);
		} catch {
			return fallback;
		}
	}

	// ── The inline "add a trigger" form ──────────────────────────────────────
	let adding = $state(false);
	/** Nothing to show below the title: no triggers, not adding one, and nothing to report. Kept
	 *  strict about `loading`/`loadFailed` so collapsing the body can never swallow "couldn't load
	 *  this journey's triggers" — an author would read the one-liner as fact. */
	const bare = $derived(!loading && !loadFailed && !rows.length && !adding);
	let name = $state('');
	let triggerKey = $state('contact.created');
	let conditions = $state<BuilderGroup>(toModel(null));

	const triggerItems = triggerGroups().map((group) => ({
		label: group.label,
		options: group.triggers.map((t) => ({
			value: t.key,
			// Named but flagged, exactly as the automations sheet does it: hiding half the contract
			// makes the picker look broken, and a firm that arms a parked trigger should know.
			label: isSupported(t.key) ? t.label : `${t.label} — not live yet`
		}))
	}));

	const spec = $derived(TRIGGER_BY_KEY[triggerKey]);
	const atCap = $derived(rows.length >= MAX_TRIGGERS);
	const editable = $derived(canManage && canWrite && !loadFailed);

	function openForm(): void {
		adding = true;
		name = `Start ${flowName}`;
		triggerKey = 'contact.created';
		conditions = toModel(null);
	}

	async function create(): Promise<void> {
		if (busy) return;
		busy = true;
		try {
			const res = await fetch(endpoint, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					name: name.trim() || `Start ${flowName}`,
					trigger_key: triggerKey,
					conditions: toJson(conditions),
					flow_uid: flowUid,
					enabled: true
				})
			});
			if (!res.ok) {
				toast.error(await message(res, 'That trigger could not be armed.'));
				return;
			}
			toast.success('Trigger armed.');
			adding = false;
			await reload();
		} finally {
			busy = false;
		}
	}

	async function setArmed(row: AutomationRow, enabled: boolean): Promise<void> {
		const res = await fetch(endpoint, {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ uid: row.uid, enabled })
		});
		if (!res.ok) {
			toast.error(await message(res, 'That trigger could not be changed.'));
			return;
		}
		toast.success(enabled ? 'Trigger armed.' : 'Trigger paused.');
		await reload();
	}

	async function remove(row: AutomationRow): Promise<void> {
		const res = await fetch(`${endpoint}?uid=${encodeURIComponent(row.uid)}`, { method: 'DELETE' });
		if (!res.ok) {
			toast.error(await message(res, 'That trigger could not be removed.'));
			return;
		}
		toast.success('Trigger removed.');
		await reload();
	}
</script>

{#if permitted}
	<!-- The empty case is ONE row, not a panel. Most journeys are started by hand and never carry a
	     trigger, so the commonest state of this card was a lightning icon, a heading, a two-line
	     paragraph and a button — about 180px of nothing, sitting between the toolbar and the canvas,
	     which is the surface the author actually came here for. -->
	<Card class="gap-2">
		<CardHeader>
			<div class="flex items-center gap-3">
				<CardTitle class="shrink-0 text-sm">Starts when…</CardTitle>
				{#if bare}
					<p class="text-muted-foreground min-w-0 flex-1 truncate text-sm">
						Nothing — it is started by hand.
					</p>
					{#if editable}
						<Button variant="outline" size="sm" class="shrink-0" onclick={openForm}>
							<PlusIcon class="size-4" /> Add a trigger
						</Button>
					{/if}
				{/if}
			</div>
		</CardHeader>
		<CardContent class={bare ? 'hidden' : 'space-y-3'}>
			{#if loading}
				<p class="text-muted-foreground text-sm">Checking what starts this journey…</p>
			{:else if loadFailed}
				<Alert variant="destructive">
					<AlertDescription>
						Couldn’t load this journey’s triggers.
						<Button variant="link" size="xs" class="px-1" onclick={reload}>Try again</Button>
					</AlertDescription>
				</Alert>
			{/if}

			{#each rows as row (row.uid)}
				<div class="space-y-1.5 rounded-lg border p-3" data-trigger={row.uid}>
					<div class="flex items-center justify-between gap-2">
						<div class="min-w-0">
							<p class="truncate text-sm font-medium">{row.name}</p>
							<p class="text-muted-foreground text-xs">{row.trigger_label ?? row.trigger_key}</p>
						</div>
						<div class="flex shrink-0 items-center gap-1">
							{#if !row.supported}
								<StatusBadge tone="warning">Not live yet</StatusBadge>
							{:else if !row.enabled}
								<StatusBadge tone="neutral">Paused</StatusBadge>
							{/if}
							{#if editable}
								<Switch
									checked={row.enabled}
									aria-label={row.enabled ? 'Pause this trigger' : 'Arm this trigger'}
									onCheckedChange={(v) => setArmed(row, v === true)}
								/>
								<Button
									variant="ghost"
									size="icon-xs"
									class="text-muted-foreground hover:text-destructive"
									aria-label="Remove this trigger"
									onclick={() => remove(row)}
								>
									<Trash2Icon class="size-3.5" />
								</Button>
							{/if}
						</div>
					</div>
					<p class="text-muted-foreground text-xs">{summariseConditions(row.conditions)}</p>
				</div>
			{/each}

			{#if adding}
				<div class="space-y-3 rounded-lg border p-3">
					<div class="space-y-1.5">
						<Label for="trigger-name">Name</Label>
						<Input
							id="trigger-name"
							bind:value={name}
							maxlength={120}
							placeholder={`Start ${flowName}`}
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="trigger-when">When</Label>
						<SelectMenu
							id="trigger-when"
							size="sm"
							items={triggerItems}
							bind:value={triggerKey}
							placeholder="Choose a trigger"
						/>
						{#if spec && !isSupported(spec.key)}
							<Alert>
								<AlertDescription>
									Nothing emits this trigger yet, so it will be stored but won’t start anything
									until that signal lands.
								</AlertDescription>
							</Alert>
						{/if}
					</div>
					<div class="space-y-1.5">
						<Label>Only if</Label>
						<ConditionBuilder model={conditions} onchange={(next) => (conditions = next)} />
					</div>
					<div class="flex justify-end gap-2">
						<Button variant="ghost" size="sm" onclick={() => (adding = false)}>Cancel</Button>
						<Button size="sm" disabled={busy} onclick={create}>
							{busy ? 'Arming…' : 'Arm trigger'}
						</Button>
					</div>
				</div>
			{/if}

			{#if editable && !adding && !bare}
				<Button variant="outline" size="sm" disabled={atCap} onclick={openForm}>
					<PlusIcon class="size-4" /> Add a trigger
				</Button>
				{#if atCap}
					<p class="text-muted-foreground text-xs">
						A journey takes at most {MAX_TRIGGERS} triggers. Widen one of them with conditions rather
						than adding a fifth.
					</p>
				{/if}
			{/if}

			{#if canManage && !canWrite && !loadFailed && !loading}
				<p class="text-muted-foreground text-xs">
					You can see what starts this journey but not change it — that needs the automations
					permission.
				</p>
			{/if}
		</CardContent>
	</Card>
{/if}
