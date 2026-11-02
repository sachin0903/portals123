<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Input,
		Label,
		ConfirmDialog,
		PageHeader,
		Switch,
		Textarea,
		buttonVariants
	} from '@graphene/ui';
	import CheckIcon from '@lucide/svelte/icons/circle-check-big';
	import CircleIcon from '@lucide/svelte/icons/circle';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import BookOpenIcon from '@lucide/svelte/icons/book-open';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { showsOutboundEmailGuard } from '$lib/instance-stage';
	import AllowlistField from '$lib/allowlist-field.svelte';
	import { can } from '$lib/permissions';
	import Readiness from '$lib/tenants/readiness.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	// TEMPORARY, with the reseed card below.
	let reseedOpen = $state(false);
	let reseeding = $state(false);
	let reseedForm = $state<HTMLFormElement | null>(null);
	let dataDialogOpen = $state(false);
	let resetForm = $state<HTMLFormElement | null>(null);
	let confirmAlias = $state('');
	let resetKind = $state<'reset' | 'reset_and_seed' | 'seed_book'>('reset');
	const tenant = $derived(data.tenant);
	const base = $derived(`/tenants/${tenant.uid}`);
	// The book (the firm's clients/accounts/transactions, read-only) has its own operations
	// permission — an operator who can configure the firm may still not read its clients.
	const canBook = $derived(can(data.permissions, data.role, 'book:read'));

	// Storage provisioning: the registry row, overlaid with the per-step result of a
	// provision run when one just happened (form.provisioned.steps).
	const storage = $derived(form?.provisioned ?? data.storage);

	// The firm's AI entitlement, overlaid with what a just-saved form returned so the card reflects
	// the change without a reload. Null => this deployment does not offer AI actions at all.
	const ai = $derived(form?.ai ?? data.ai);
	const emailAllowlist = $derived(form?.emailAllowlist ?? data.emailAllowlist);
	const allowlistKey = $derived((emailAllowlist?.entries ?? []).join('\n'));
	const defaultAllowlist = $derived(
		emailAllowlist?.default_entries.length
			? emailAllowlist.default_entries.join(', ')
			: 'an empty list, which sends nothing'
	);
	const dataJob = $derived(form?.dataJob ?? data.dataJob);
	const jobStatusLabel = (value: string) =>
		({ queued: 'Queued', processing: 'Running', completed: 'Completed', failed: 'Failed' })[
			value
		] ?? 'Unknown';
	const jobPhaseLabel = (value: string) => {
		if (dataJob?.kind === 'seed_book') {
			return (
				{
					queued: 'Waiting for worker',
					database: 'Clearing demo book',
					database_done: 'Demo book cleared',
					seed_done: 'Demo book seeded',
					completed: 'Completed',
					failed: 'Failed'
				}[value] ?? 'Working'
			);
		}
		return (
			{
				queued: 'Waiting for worker',
				database: 'Clearing database',
				database_done: 'Database cleared',
				object_storage: 'Clearing object storage',
				object_storage_done: 'Object storage cleared',
				lake_done: 'Lake cleared',
				seed_done: 'Demo data seeded',
				completed: 'Completed',
				failed: 'Failed'
			}[value] ?? 'Working'
		);
	};

	$effect(() => {
		if (dataJob?.status !== 'queued' && dataJob?.status !== 'processing') return;
		const timer = window.setInterval(() => void invalidateAll(), 2000);
		return () => window.clearInterval(timer);
	});

	// Onboarding checklist, derived from the firm's WorkOS configuration + members.
	const env = $derived(data.environment);
	const steps = $derived([
		{ label: 'Firm created', done: true, href: null },
		{
			label: 'Configure WorkOS',
			done: !!env,
			href: `${base}/settings/sign-in`
		},
		{
			label: 'Set the WorkOS keys',
			done: !!env?.has_secrets,
			href: `${base}/settings/sign-in`
		},
		{
			label: 'Create the portal orgs',
			done: !!env?.firm_org_id,
			href: `${base}/settings/sign-in`
		},
		{ label: 'Invite members', done: data.members.length > 0, href: `${base}/members` },
		{ label: 'Provision storage', done: storage?.status === 'active', href: null }
	]);
	const remaining = $derived(steps.filter((s) => !s.done));
</script>

<PageHeader
	title={tenant.name}
	description="{tenant.alias} · {tenant.workos_project_id ?? 'no WorkOS project'}"
>
	{#snippet actions()}
		<!-- The firm's own enabled/disabled state, which lived on the at-a-glance panel this page no
		     longer has. It belongs beside the name rather than in a list of figures: it is the one
		     fact that qualifies every other number on the page. -->
		<Badge variant={tenant.enabled ? 'secondary' : 'destructive'}>
			{tenant.enabled ? 'Active' : 'Disabled'}
		</Badge>
	{/snippet}
</PageHeader>

{#if showsOutboundEmailGuard(data.appStage)}
	<Card class="mt-6">
		<CardHeader>
			<CardTitle>Outbound email</CardTitle>
			<CardDescription>
				This environment sends email only to the addresses below, for every firm. Any other address
				is recorded and not sent.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			{#if form?.emailError}
				<Alert variant="destructive">
					<AlertDescription>{form.emailError}</AlertDescription>
				</Alert>
			{/if}
			{#if !emailAllowlist}
				<p class="text-muted-foreground text-sm">
					The allowlist could not be loaded. This service may not be restricting outbound email.
				</p>
			{:else if data.canManage}
				<form method="POST" action="?/setEmailAllowlist" class="space-y-3" use:enhance>
					<div class="space-y-2">
						<Label for="email-allowlist">Send to</Label>
						{#key allowlistKey}
							<AllowlistField initial={emailAllowlist.entries} />
						{/key}
						<p class="text-muted-foreground text-xs">
							Paste a list. Commas, spaces and new lines all work. The default is {defaultAllowlist}.
							Remove every address and save to send nothing.
							{#if emailAllowlist.using_default}
								Using the default.
							{/if}
						</p>
					</div>
					<div class="flex flex-wrap gap-2">
						<Button type="submit" variant="secondary" size="sm">Save</Button>
						{#if !emailAllowlist.using_default}
							<Button type="submit" name="use_default" value="1" variant="ghost" size="sm">
								Reset to default
							</Button>
						{/if}
					</div>
				</form>
			{:else if emailAllowlist.entries.length === 0}
				<p class="text-sm">Nothing is sent.</p>
			{:else}
				<AllowlistField initial={emailAllowlist.entries} readonly />
				<p class="text-muted-foreground text-sm">
					Changing this needs the tenants:write permission.
				</p>
			{/if}
		</CardContent>
	</Card>
{/if}

<!-- Onboarding first and on its own row while it still has steps: it is the only thing on
     this page that is a TASK LIST rather than a status, so it leads for a new firm and
     disappears once the firm is set up — a checklist with nothing left to check is noise.
     The at-a-glance panel is gone: members, WorkOS and status all read off the readiness
     cards now, and more precisely than a three-row summary managed. -->
{#if form?.dataError}
	<Alert variant="destructive" class="mb-6">
		<AlertDescription>{form.dataError}</AlertDescription>
	</Alert>
{:else if form?.providersResumed}
	<Alert class="mb-6">
		<AlertDescription>Provider refreshes resumed.</AlertDescription>
	</Alert>
{:else if form?.error}
	<!-- Storage provisioning reports here: the card that used to show its outcome is gone, and a
	     repair button with no visible result is indistinguishable from one that does nothing. -->
	<Alert variant="destructive" class="mb-6">
		<AlertDescription>{form.error}</AlertDescription>
	</Alert>
{:else if form?.provisioned}
	<Alert class="mb-6">
		<AlertDescription>Storage re-provisioned.</AlertDescription>
	</Alert>
{:else if form?.reseeded}
	<!-- TEMPORARY, with the reseed card. `updated: false` is the common answer and means the
	     firm already matches the shipped seed -- which must not read as a failure. -->
	<Alert class="mb-6">
		<AlertDescription>
			{form.reseeded.updated || form.reseeded.created
				? `Onboarding updated. ${form.reseeded.in_flight} active journey${form.reseeded.in_flight === 1 ? '' : 's'} kept ${form.reseeded.in_flight === 1 ? 'its' : 'their'} current version.`
				: 'Onboarding is already up to date. Nothing changed.'}
		</AlertDescription>
	</Alert>
{/if}

{#if data.canManage && data.dataToolsAvailable}
	<Card class="border-destructive/40 mt-6 border-dashed">
		<CardHeader>
			<div class="flex items-center justify-between gap-4">
				<div>
					<CardTitle>Data tools</CardTitle>
					<CardDescription>Reset or replace non-production firm data.</CardDescription>
				</div>
				{#if dataJob}
					<Badge variant={dataJob.status === 'failed' ? 'destructive' : 'secondary'}>
						{jobStatusLabel(dataJob.status)}
					</Badge>
				{/if}
			</div>
		</CardHeader>
		<CardContent class="space-y-4">
			<p class="text-muted-foreground text-sm">
				Reset removes clients, accounts, transactions, holdings, files and journey runs. Seed demo
				book replaces only the sample clients and their accounts, transactions and holdings — real
				clients, documents, staff and authored flows stay. Firm settings, roles, branding, product
				configuration and audit history are kept either way. Provider refreshes stay paused until
				you resume them.
			</p>

			{#if dataJob}
				<div class="bg-muted/40 rounded-lg border p-3 text-sm">
					<p class="font-medium">
						Phase:
						{jobPhaseLabel(dataJob.phase)}
					</p>
					{#if dataJob.error}
						<p class="text-destructive mt-1">{dataJob.error}</p>
					{/if}
				</div>
			{/if}

			<div class="max-w-sm space-y-2">
				<Label for="reset-confirm">Type {tenant.alias} to confirm</Label>
				<Input
					id="reset-confirm"
					placeholder={tenant.alias}
					autocomplete="off"
					bind:value={confirmAlias}
				/>
			</div>

			<form method="POST" action="?/resetData" bind:this={resetForm} use:enhance>
				<input type="hidden" name="confirm_alias" value={confirmAlias} />
				<input type="hidden" name="kind" value={resetKind} />
			</form>
			<div class="flex flex-wrap gap-2">
				<Button
					type="button"
					variant="destructive"
					disabled={tenant.enabled ||
						confirmAlias !== tenant.alias ||
						dataJob?.status === 'queued' ||
						dataJob?.status === 'processing'}
					onclick={() => {
						resetKind = 'reset';
						dataDialogOpen = true;
					}}>Reset data</Button
				>
				<Button
					type="button"
					variant="destructive"
					disabled={tenant.enabled ||
						confirmAlias !== tenant.alias ||
						dataJob?.status === 'queued' ||
						dataJob?.status === 'processing'}
					onclick={() => {
						resetKind = 'reset_and_seed';
						dataDialogOpen = true;
					}}>Reset and add sample data</Button
				>
				<Button
					type="button"
					variant="secondary"
					disabled={confirmAlias !== tenant.alias ||
						dataJob?.status === 'queued' ||
						dataJob?.status === 'processing'}
					onclick={() => {
						resetKind = 'seed_book';
						dataDialogOpen = true;
					}}>Seed demo book</Button
				>
				{#if dataJob?.providers_paused && !['queued', 'processing'].includes(dataJob.status)}
					<form method="POST" action="?/resumeProviders" use:enhance>
						<Button type="submit" variant="secondary">Resume provider refreshes</Button>
					</form>
				{/if}
			</div>
			{#if tenant.enabled}
				<p class="text-destructive text-xs">
					Suspend this firm before a full reset. Seed demo book can run while it is active.
				</p>
			{/if}
		</CardContent>
	</Card>

	<ConfirmDialog
		bind:open={dataDialogOpen}
		destructive={resetKind !== 'seed_book'}
		title={resetKind === 'seed_book'
			? `Replace the demo book for ${tenant.name}?`
			: resetKind === 'reset_and_seed'
				? `Reset and add sample data for ${tenant.name}?`
				: `Reset ${tenant.name}'s data?`}
		description={resetKind === 'seed_book'
			? 'Replaces the sample clients (Amelia, Noah, and the rest) and their accounts, transactions and holdings. Real clients, documents, staff and authored flows are left alone.'
			: "This permanently removes the firm's clients, accounts, transactions, files and journeys. Firm configuration and audit history remain."}
		confirmLabel={resetKind === 'seed_book'
			? 'Seed demo book'
			: resetKind === 'reset_and_seed'
				? 'Reset and add sample data'
				: 'Reset data'}
		onconfirm={() => resetForm?.requestSubmit()}
	/>
{/if}

{#if remaining.length > 0}
	<div class="mb-6 grid gap-6 lg:grid-cols-2">
		<!-- Onboarding checklist -->
		<Card glass>
			<CardHeader>
				<CardTitle>Onboarding</CardTitle>
				<CardDescription>
					{#if remaining.length === 0}
						This firm is fully set up.
					{:else}
						{remaining.length} step{remaining.length === 1 ? '' : 's'} left to go live.
					{/if}
				</CardDescription>
			</CardHeader>
			<CardContent class="space-y-1">
				{#each steps as step (step.label)}
					<div class="flex items-center gap-3 rounded-lg px-2 py-2">
						{#if step.done}
							<CheckIcon class="text-positive size-5 shrink-0" />
						{:else}
							<CircleIcon class="text-muted-foreground size-5 shrink-0" />
						{/if}
						<span class={step.done ? 'text-muted-foreground text-sm line-through' : 'text-sm'}>
							{step.label}
						</span>
						{#if !step.done && step.href}
							<Button variant="link" size="sm" href={step.href} class="ml-auto">
								Do it <ArrowRightIcon class="size-3.5" />
							</Button>
						{/if}
					</div>
				{/each}
			</CardContent>
		</Card>
	</div>
{/if}

<!-- Readiness at a glance, above the detail: the first question about a firm is whether it is
     actually working, and answering it used to mean visiting four sub-pages. Omitted rather than
     drawn grey when the API predates the endpoint. -->
{#if data.overview}
	<Readiness overview={data.overview} tenantUid={tenant.uid} />
{/if}

<!-- The firm's book — the waterfall's top tier, gated on book:read (not tenants:read). -->
{#if canBook}
	<Card glass class="mt-6">
		<CardHeader>
			<CardTitle class="flex items-center gap-2">
				<BookOpenIcon class="size-4" /> Book
			</CardTitle>
			<CardDescription>
				View this firm's clients, accounts and transactions. Every access is recorded under your
				name.
			</CardDescription>
		</CardHeader>
		<CardContent class="flex flex-wrap gap-2">
			<a href="{base}/book/clients" class={buttonVariants({ variant: 'secondary', size: 'sm' })}>
				Clients
			</a>
			<a href="{base}/book/accounts" class={buttonVariants({ variant: 'secondary', size: 'sm' })}>
				Accounts
			</a>
			<a
				href="{base}/book/transactions"
				class={buttonVariants({ variant: 'secondary', size: 'sm' })}
			>
				Transactions
			</a>
		</CardContent>
	</Card>
{/if}

<!-- AI actions: the per-firm half of the two switches (app/flows/ai.py) -->
<Card glass class="mt-6">
	<CardHeader>
		<CardTitle class="flex items-center gap-2">
			<SparklesIcon class="size-4" /> AI actions
		</CardTitle>
		<CardDescription>
			Lets this firm's journeys use the <strong>classify</strong> and
			<strong>draft&nbsp;a&nbsp;message</strong> actions. Drafts always require a person to review and
			send them. Only fields selected by the journey author are shared.
		</CardDescription>
	</CardHeader>
	<CardContent class="space-y-4">
		{#if form?.aiError}
			<Alert variant="destructive">
				<AlertDescription>{form.aiError}</AlertDescription>
			</Alert>
		{/if}
		{#if !ai}
			<p class="text-muted-foreground text-sm">AI actions are not available on this platform.</p>
		{:else}
			{#if !ai.configured}
				<!-- The deployment half. An "enabled" firm on a keyless deployment is a setting that
				     does nothing, and an operator should see that here rather than in the deploy config. -->
				<Alert>
					<AlertDescription>
						AI actions are unavailable in this environment. Enabling them for this firm will not
						take effect until the service is available.
					</AlertDescription>
				</Alert>
			{/if}
			<dl class="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
				<div class="flex justify-between sm:block">
					<dt class="text-muted-foreground">Status</dt>
					<dd>
						<Badge variant={ai.enabled && ai.configured ? 'secondary' : 'outline'}>
							{ai.enabled ? (ai.configured ? 'enabled' : 'enabled, no model') : 'off'}
						</Badge>
					</dd>
				</div>
				<div class="flex justify-between sm:block">
					<dt class="text-muted-foreground">Used this month</dt>
					<dd>
						{ai.tokens_this_month.toLocaleString()} / {ai.monthly_token_cap.toLocaleString()} tokens
					</dd>
				</div>
				<div class="flex justify-between sm:block">
					<dt class="text-muted-foreground">Calls this month</dt>
					<dd>{ai.calls_this_month.toLocaleString()}</dd>
				</div>
			</dl>
			{#if data.canManage}
				<form method="POST" action="?/setAi" class="space-y-4" use:enhance>
					<div class="flex items-center gap-3">
						<Switch id="ai-enabled" name="enabled" checked={ai.enabled} />
						<Label for="ai-enabled">Allow this firm's journeys to use AI actions</Label>
					</div>
					<div class="grid gap-4 sm:grid-cols-2">
						<div class="space-y-2">
							<Label for="ai-cap">Monthly token allowance</Label>
							<Input
								id="ai-cap"
								name="monthly_token_cap"
								type="number"
								min="1"
								value={ai.monthly_token_cap}
							/>
							<p class="text-muted-foreground text-xs">
								Per calendar month. Journeys pause when the limit is reached and can be run again
								after the allowance resets or changes.
							</p>
						</div>
						<div class="space-y-2">
							<Label for="ai-notes">Note</Label>
							<Textarea
								id="ai-notes"
								name="notes"
								rows={3}
								placeholder="Who asked for this, what for, who approved it."
							/>
						</div>
					</div>
					<Button type="submit" variant="secondary" size="sm">Save</Button>
				</form>
			{:else}
				<p class="text-muted-foreground text-sm">
					Changing this needs the tenants:write permission.
				</p>
			{/if}
		{/if}
	</CardContent>
</Card>

<!-- TEMPORARY (Sept 2026). Brings a firm seeded weeks ago up to the onboarding definition this
     build ships. A firm is seeded once, at creation, so one created before a seed change is frozen
     at the definition that shipped that day. Delete this card with the form action and the API
     route. -->
{#if data.canManage}
	<Card class="border-dashed mt-6">
		<CardHeader>
			<CardTitle>Onboarding flow</CardTitle>
			<CardDescription>
				Update this firm's onboarding journey to the current standard version. This action is not
				available in production.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			<p class="text-muted-foreground text-sm">
				Active journeys keep their current version. The six standard onboarding forms are replaced,
				so firm changes to those forms will be lost. Existing answers are kept.
			</p>
			<form
				method="POST"
				action="?/reseedOnboarding"
				bind:this={reseedForm}
				use:enhance={() => {
					reseeding = true;
					return async ({ result, update }) => {
						reseeding = false;
						await update({ reset: false });
						if (result.type !== 'success') return;
					};
				}}
			></form>
			<Button
				type="button"
				variant="secondary"
				size="sm"
				disabled={reseeding}
				onclick={() => (reseedOpen = true)}
			>
				{reseeding ? 'Updating…' : 'Update onboarding'}
			</Button>
		</CardContent>
	</Card>

	<ConfirmDialog
		bind:open={reseedOpen}
		title="Update {tenant.name}'s onboarding flow?"
		description="This publishes the current standard onboarding journey. Active journeys keep their current version. Firm changes to the six standard forms will be replaced, but existing answers are kept."
		confirmLabel="Update"
		onconfirm={() => reseedForm?.requestSubmit()}
	/>
{/if}
