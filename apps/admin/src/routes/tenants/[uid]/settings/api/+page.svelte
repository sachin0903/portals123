<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Badge,
		britishDate,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Checkbox,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		humanise,
		Input,
		Label,
		LoadErrorBanner,
		StatusBadge,
		type StatusTone,
		timestampParts,
		toast
	} from '@graphene/ui';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import type { ActionResult, SubmitFunction } from '@sveltejs/kit';
	import { copyToClipboard } from '$lib/invitation';
	import type { PageData } from './$types';
	import type { ApiKey, WebhookDelivery } from './+page.server';

	let { data }: { data: PageData } = $props();

	const KEY_TONE: Record<ApiKey['status'], StatusTone> = {
		active: 'success',
		expired: 'warning',
		revoked: 'neutral'
	};
	const DELIVERY_TONE: Record<WebhookDelivery['status'], StatusTone> = {
		pending: 'info',
		sent: 'success',
		failed: 'danger'
	};
	const KIND_LABEL: Record<NonNullable<WebhookDelivery['kind']>, string> = {
		identity: 'Identity',
		aml: 'AML'
	};

	const keyColumns: DataTableColumn<ApiKey>[] = [
		{ key: 'name', header: 'Name', cellClass: 'font-medium' },
		{ key: 'prefix', header: 'Prefix' },
		{ key: 'scopes', header: 'Scopes' },
		{ key: 'status', header: 'Status' },
		{ key: 'created', header: 'Created', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'last_used', header: 'Last used', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'actions', header: '', align: 'right' }
	];
	const deliveryColumns: DataTableColumn<WebhookDelivery>[] = [
		{ key: 'created', header: 'Created', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'check', header: 'Check' },
		{ key: 'kind', header: 'Kind', cellClass: 'text-sm' },
		{ key: 'check_status', header: 'Check status', cellClass: 'text-sm' },
		{ key: 'status', header: 'Delivery' },
		{ key: 'attempts', header: 'Attempts', align: 'right', cellClass: 'text-sm tabular-nums' },
		{ key: 'timing', header: 'Next attempt / sent', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'error', header: 'Last error', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'actions', header: '', align: 'right' }
	];

	// --- One-time secrets ------------------------------------------------------------------------
	// A secret reaches this component's state and nowhere else: not the URL, not the load data and
	// not `page.form` (the secret actions skip `update()`, which would apply the result there).
	// Done drops it, and leaving the page drops it with the component.
	let newKey = $state<{ name: string; secret: string } | null>(null);
	let webhookSecret = $state<string | null>(null);

	async function copySecret(secret: string, what: string): Promise<void> {
		if (await copyToClipboard(secret)) toast.success(`${what} copied to the clipboard.`);
		else toast.error('Clipboard access was denied. Select the secret and copy it manually.');
	}

	// --- Submissions -----------------------------------------------------------------------------
	// Pending flags are set inside the enhance callback, where the submission is already a fact
	// (docs/gotchas.md: a submitter disabled by its own click never submits).
	let pending = $state<Record<string, boolean>>({});

	function reportFailure(result: ActionResult): void {
		if (result.type === 'redirect') return;
		toast.error(
			result.type === 'failure'
				? String(result.data?.error ?? 'Something went wrong.')
				: 'Something went wrong.'
		);
	}

	function submitWith(key: string, success: string, onSuccess?: () => void): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					onSuccess?.();
					toast.success(success);
					await update({ reset: false });
				} else {
					reportFailure(result);
					await update();
				}
			};
		};
	}

	function submitForSecret(
		key: string,
		success: string,
		onSecret: (secret: string, result: Record<string, unknown>) => void
	): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					onSecret(String(result.data?.secret ?? ''), result.data ?? {});
					toast.success(success);
					await invalidateAll();
				} else {
					reportFailure(result);
					await update();
				}
			};
		};
	}

	// --- Create key ------------------------------------------------------------------------------
	// Bound state rather than a native form reset: a `Checkbox` posts through a hidden input that a
	// `form.reset()` would untick behind the visible box.
	let keyName = $state('');
	let keyExpiry = $state('');
	let excludedScopes = $state<string[]>([]);
	const chosenScopes = $derived(data.scopes.filter((s) => !excludedScopes.includes(s)));
	const today = new Date().toISOString().slice(0, 10);

	function toggleScope(scope: string, on: boolean): void {
		excludedScopes = on
			? excludedScopes.filter((s) => s !== scope)
			: [...excludedScopes.filter((s) => s !== scope), scope];
	}

	const createKey = submitForSecret('create', 'Key created.', (secret, result) => {
		const key = result.key as ApiKey | undefined;
		newKey = { name: key?.name ?? keyName.trim(), secret };
		keyName = '';
		keyExpiry = '';
		excludedScopes = [];
	});

	// --- Revoke key ------------------------------------------------------------------------------
	let revokeForm = $state<HTMLFormElement | null>(null);
	let revoking = $state<ApiKey | null>(null);
	let revokeOpen = $state(false);

	// --- Webhook ---------------------------------------------------------------------------------
	let webhookForm = $state<HTMLFormElement | null>(null);
	let removeForm = $state<HTMLFormElement | null>(null);
	let webhookUrl = $state('');
	let replaceOpen = $state(false);
	let removeOpen = $state(false);
	// Replacing a set URL also replaces its signing secret, so that submit waits for the confirm.
	// The check runs after the browser's own URL validation, and Enter in the field goes through it.
	let replaceConfirmed = false;

	const saveWebhook = submitForSecret('webhook', 'Webhook saved.', (secret) => {
		webhookSecret = secret;
		webhookUrl = '';
	});
	const setWebhook: SubmitFunction = (input) => {
		if (data.webhook?.url && !replaceConfirmed) {
			input.cancel();
			replaceOpen = true;
			return;
		}
		replaceConfirmed = false;
		return saveWebhook(input);
	};
</script>

{#snippet secretPanel(
	title: string,
	about: string,
	secret: string,
	what: string,
	ondone: () => void
)}
	<Alert variant="warning">
		<KeyRoundIcon />
		<AlertTitle>{title}</AlertTitle>
		<AlertDescription class="space-y-2">
			<p>{about}</p>
			<code
				class="bg-background text-foreground block rounded-md border px-2 py-1.5 font-mono text-xs break-all"
				>{secret}</code
			>
			<div class="flex flex-wrap items-center gap-2">
				<Button
					type="button"
					variant="secondary"
					size="sm"
					onclick={() => copySecret(secret, what)}
				>
					Copy
				</Button>
				<Button type="button" variant="ghost" size="sm" onclick={ondone}>Done</Button>
			</div>
		</AlertDescription>
	</Alert>
{/snippet}

<!-- The heading is the Settings layout's; this panel is its API section. -->
<div class="space-y-4">
	<Card glass>
		<CardHeader>
			<CardTitle>API keys</CardTitle>
			<CardDescription>
				Keys for the firm's own systems to call the Graphene API. A secret is shown once, when its
				key is created.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			{#if data.keysError}
				<LoadErrorBanner message={data.keysError} onRetry={() => invalidateAll()} />
			{:else}
				{#if newKey}
					{@render secretPanel(
						`Copy the secret for ${newKey.name} now`,
						'It is shown only this once and cannot be fetched again. Store it in the firm’s secrets manager.',
						newKey.secret,
						'Key secret',
						() => (newKey = null)
					)}
				{/if}

				<DataTable
					columns={keyColumns}
					rows={data.keys}
					getKey={(k) => k.uid}
					label="API keys"
					empty="No API keys yet."
				>
					{#snippet cell(k, column)}
						{#if column.key === 'name'}
							{k.name}
						{:else if column.key === 'prefix'}
							<code class="font-mono text-xs">{k.key_prefix}…</code>
						{:else if column.key === 'scopes'}
							<div class="flex flex-wrap gap-1">
								{#each k.scopes as scope (scope)}
									<Badge variant="outline" class="font-mono">{scope}</Badge>
								{/each}
							</div>
						{:else if column.key === 'status'}
							<div class="space-y-0.5">
								<StatusBadge tone={KEY_TONE[k.status] ?? 'neutral'}
									>{humanise(k.status)}</StatusBadge
								>
								{#if k.status === 'revoked' && k.revoked_at}
									<div class="text-muted-foreground text-xs">{britishDate(k.revoked_at)}</div>
								{:else if k.expires_at}
									<div class="text-muted-foreground text-xs">
										{k.status === 'expired' ? 'Expired' : 'Expires'}
										{britishDate(k.expires_at)}
									</div>
								{/if}
							</div>
						{:else if column.key === 'created'}
							{britishDate(k.created_at)}
						{:else if column.key === 'last_used'}
							{#if k.last_used_at}
								{@const at = timestampParts(k.last_used_at)}
								<span title={at.relative ?? undefined}>{at.absolute}</span>
							{:else}
								Never
							{/if}
						{:else if column.key === 'actions'}
							{#if k.status === 'active'}
								<Button
									type="button"
									variant="ghost"
									size="sm"
									class="text-destructive"
									disabled={!data.canManage || pending.revoke}
									onclick={() => {
										revoking = k;
										revokeOpen = true;
									}}
								>
									{pending.revoke && revoking?.uid === k.uid ? 'Revoking…' : 'Revoke'}
								</Button>
							{/if}
						{/if}
					{/snippet}
				</DataTable>

				<form
					method="POST"
					action="?/createKey"
					use:enhance={createKey}
					class="space-y-3 border-t pt-4"
				>
					<div class="text-sm font-medium">Create key</div>
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="space-y-1">
							<Label for="key_name">Name</Label>
							<Input
								id="key_name"
								name="name"
								bind:value={keyName}
								placeholder="CRM integration"
								maxlength={100}
								required
								autocomplete="off"
								disabled={!data.canManage}
							/>
						</div>
						<div class="space-y-1">
							<Label for="key_expires_at">
								Expires <span class="text-muted-foreground">(optional)</span>
							</Label>
							<Input
								id="key_expires_at"
								name="expires_at"
								type="date"
								min={today}
								bind:value={keyExpiry}
								disabled={!data.canManage}
							/>
						</div>
					</div>
					<fieldset class="space-y-2">
						<legend class="text-sm font-medium">Scopes</legend>
						<div class="flex flex-wrap gap-x-4 gap-y-2">
							{#each data.scopes as scope (scope)}
								<label class="flex items-center gap-2 text-sm">
									<Checkbox
										name="scope"
										value={scope}
										checked={!excludedScopes.includes(scope)}
										disabled={!data.canManage}
										onCheckedChange={(on) => toggleScope(scope, on === true)}
									/>
									<span class="font-mono text-xs">{scope}</span>
								</label>
							{/each}
						</div>
					</fieldset>
					<Button
						type="submit"
						size="sm"
						disabled={!data.canManage || pending.create || chosenScopes.length === 0}
					>
						{pending.create ? 'Creating…' : 'Create key'}
					</Button>
				</form>
			{/if}
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Webhook</CardTitle>
			<CardDescription>
				Graphene posts each check update to this address, signed with the webhook's secret.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			{#if data.webhookError}
				<LoadErrorBanner message={data.webhookError} onRetry={() => invalidateAll()} />
			{:else}
				{#if webhookSecret}
					{@render secretPanel(
						'Copy the signing secret now',
						'It is shown only this once. Setting the URL again replaces it, and the firm must then update its signature check.',
						webhookSecret,
						'Signing secret',
						() => (webhookSecret = null)
					)}
				{/if}

				<div class="flex flex-wrap items-center justify-between gap-2">
					<div class="min-w-0 space-y-0.5">
						<div class="text-muted-foreground text-xs font-medium">Current URL</div>
						{#if data.webhook?.url}
							<code class="font-mono text-sm break-all">{data.webhook.url}</code>
						{:else}
							<div class="text-muted-foreground text-sm">Not set</div>
						{/if}
					</div>
					{#if data.webhook?.url}
						<Button
							type="button"
							variant="destructive"
							size="sm"
							disabled={!data.canManage || pending.remove}
							onclick={() => (removeOpen = true)}
						>
							{pending.remove ? 'Removing…' : 'Remove'}
						</Button>
					{/if}
				</div>

				<form
					method="POST"
					action="?/setWebhook"
					use:enhance={setWebhook}
					bind:this={webhookForm}
					class="space-y-1 border-t pt-4"
				>
					<Label for="webhook_url">{data.webhook?.url ? 'Replace URL' : 'Webhook URL'}</Label>
					<div class="flex flex-col gap-2 sm:flex-row">
						<Input
							id="webhook_url"
							name="url"
							type="url"
							bind:value={webhookUrl}
							placeholder="https://firm.example.com/graphene/webhook"
							required
							autocomplete="off"
							class="sm:flex-1"
							disabled={!data.canManage}
						/>
						<Button type="submit" size="sm" disabled={!data.canManage || pending.webhook}>
							{pending.webhook ? 'Saving…' : data.webhook?.url ? 'Replace webhook' : 'Set webhook'}
						</Button>
					</div>
				</form>
				<p class="text-muted-foreground text-xs">
					Only HTTPS to a public address. Sends time out after 10 seconds and do not follow
					redirects. Each delivery carries an
					<code class="font-mono">X-Graphene-Signature</code> header:
					<code class="font-mono"
						>t=&lt;unix&gt;,v1=&lt;hex HMAC-SHA256 of "&lt;t&gt;.&lt;body&gt;"&gt;</code
					>.
				</p>
			{/if}
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Recent deliveries</CardTitle>
			<CardDescription>
				A failed send is retried after 1 minute, 5 minutes, 30 minutes, 2 hours, 6 hours and 24
				hours, then marked failed.
			</CardDescription>
		</CardHeader>
		<CardContent>
			{#if data.deliveriesError}
				<LoadErrorBanner message={data.deliveriesError} onRetry={() => invalidateAll()} />
			{:else}
				<DataTable
					columns={deliveryColumns}
					rows={data.deliveries}
					getKey={(d) => d.uid}
					label="Recent deliveries"
					empty="No deliveries yet."
				>
					{#snippet cell(d, column)}
						{#if column.key === 'created'}
							{@const at = timestampParts(d.created_at)}
							<span title={at.relative ?? undefined}>{at.absolute}</span>
						{:else if column.key === 'check'}
							{#if d.check_uid}
								<code class="font-mono text-xs">{d.check_uid}</code>
							{:else}
								—
							{/if}
						{:else if column.key === 'kind'}
							{d.kind ? KIND_LABEL[d.kind] : '—'}
						{:else if column.key === 'check_status'}
							{humanise(d.check_status)}
						{:else if column.key === 'status'}
							<StatusBadge tone={DELIVERY_TONE[d.status] ?? 'neutral'}>
								{humanise(d.status)}
							</StatusBadge>
						{:else if column.key === 'attempts'}
							{d.attempts}
						{:else if column.key === 'timing'}
							{#if d.status === 'sent' && d.sent_at}
								Sent {timestampParts(d.sent_at).absolute}
							{:else if d.status === 'pending'}
								Next {timestampParts(d.next_attempt_at).absolute}
							{:else}
								—
							{/if}
						{:else if column.key === 'error'}
							{#if d.last_error}
								<span class="block max-w-[16rem] truncate" title={d.last_error}>{d.last_error}</span
								>
							{:else}
								—
							{/if}
						{:else if column.key === 'actions'}
							{#if d.status === 'failed'}
								<form
									method="POST"
									action="?/resendDelivery"
									use:enhance={submitWith(`resend:${d.uid}`, 'Delivery queued to send again.')}
								>
									<input type="hidden" name="delivery_uid" value={d.uid} />
									<Button
										type="submit"
										variant="outline"
										size="sm"
										disabled={!data.canManage || pending[`resend:${d.uid}`]}
									>
										{pending[`resend:${d.uid}`] ? 'Sending…' : 'Send again'}
									</Button>
								</form>
							{/if}
						{/if}
					{/snippet}
				</DataTable>
			{/if}
		</CardContent>
	</Card>
</div>

<form
	method="POST"
	action="?/revokeKey"
	use:enhance={submitWith('revoke', 'Key revoked.')}
	bind:this={revokeForm}
	class="hidden"
>
	<input type="hidden" name="key_uid" value={revoking?.uid ?? ''} />
</form>

<form
	method="POST"
	action="?/removeWebhook"
	use:enhance={submitWith('remove', 'Webhook removed.', () => (webhookSecret = null))}
	bind:this={removeForm}
	class="hidden"
></form>

<ConfirmDialog
	bind:open={revokeOpen}
	title={revoking ? `Revoke ${revoking.name}?` : 'Revoke this key?'}
	description="The firm API stops accepting this key within 60 seconds, and it cannot be restored."
	confirmLabel="Revoke key"
	destructive
	onconfirm={() => revokeForm?.requestSubmit()}
/>

<ConfirmDialog
	bind:open={replaceOpen}
	title="Replace the webhook?"
	description="A new signing secret replaces the current one, so the firm must update its signature check."
	confirmLabel="Replace webhook"
	onconfirm={() => {
		replaceConfirmed = true;
		webhookForm?.requestSubmit();
	}}
/>

<ConfirmDialog
	bind:open={removeOpen}
	title="Remove the webhook?"
	description="Graphene stops sending check updates and deletes the queued and past deliveries."
	confirmLabel="Remove webhook"
	destructive
	onconfirm={() => removeForm?.requestSubmit()}
/>
