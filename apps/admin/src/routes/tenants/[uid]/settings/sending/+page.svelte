<script lang="ts">
	import { enhance } from '$app/forms';
	import { downloadRecords, requiredRecordsVerified } from '$lib/tenants/sender-records';
	import {
		Alert,
		AlertDescription,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		CopyButton,
		Input,
		EmailInput,
		Label,
		LoadErrorBanner,
		StatusBadge,
		type StatusTone,
		toast
	} from '@graphene/ui';
	import type { ActionResult, SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';
	import type { SenderDomain } from './+page.server';

	let { data }: { data: PageData } = $props();

	let domainName = $state('');
	let pending = $state<Record<string, boolean>>({});
	let removeOpen = $state(false);
	let removeForm = $state<HTMLFormElement | null>(null);

	const domain = $derived(data.domain);
	const canManage = $derived(data.canManage);
	const recordsReady = $derived(domain ? requiredRecordsVerified(domain.records) : false);

	// The saved address, until the operator edits it. Re-applied when a save comes back.
	let fromAddress = $state('');
	$effect(() => {
		fromAddress = domain?.from_address ?? '';
	});

	function tone(value: SenderDomain): StatusTone {
		if (value.active) return 'success';
		if (
			value.status === 'failed' ||
			value.status === 'partially_failed' ||
			value.status === 'deleted'
		)
			return 'danger';
		if (value.sending_verified) return 'success';
		if (value.status === 'pending' || value.status === 'partially_verified') return 'warning';
		return 'neutral';
	}

	function label(value: SenderDomain): string {
		if (value.active) return 'Sending';
		if (value.sending_verified) return 'Verified';
		if (value.status === 'failed' || value.status === 'partially_failed') return 'Failed';
		if (value.status === 'deleted') return 'Removed at Resend';
		if (value.status === 'pending' || value.status === 'partially_verified') return 'Checking';
		return 'Not started';
	}

	function reportFailure(result: ActionResult): void {
		if (result.type === 'redirect') return;
		toast.error(
			result.type === 'failure'
				? String(result.data?.error ?? 'Something went wrong.')
				: 'Something went wrong.'
		);
	}

	const submitTest: SubmitFunction = () => {
		pending.test = true;
		return async ({ result, update }) => {
			pending.test = false;
			if (result.type === 'success') {
				const to = String(result.data?.to ?? 'you');
				if (result.data?.status === 'delivered') toast.success(`Test email sent to ${to}.`);
				else toast.error(String(result.data?.detail ?? 'The test email could not be sent.'));
				await update({ reset: false });
			} else {
				reportFailure(result);
				await update();
			}
		};
	};

	function submitWith(key: string, success: string): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					toast.success(success);
					await update({ reset: false });
				} else {
					reportFailure(result);
					await update();
				}
			};
		};
	}
</script>

{#if data.error}
	<LoadErrorBanner message={data.error} />
{:else if !domain}
	<Card>
		<CardHeader>
			<CardTitle>Sending domain</CardTitle>
			<CardDescription>
				Mail from this firm comes from Graphene until a domain of theirs is verified. Use a
				subdomain such as mail.example.co.uk so sending stays off the firm's own root domain.
			</CardDescription>
		</CardHeader>
		<CardContent>
			<form
				method="POST"
				action="?/register"
				use:enhance={submitWith('register', 'Domain registered.')}
				class="space-y-4"
			>
				<div class="space-y-2">
					<Label for="domain">Domain</Label>
					<Input
						id="domain"
						name="domain"
						bind:value={domainName}
						placeholder="mail.example.co.uk"
						autocomplete="off"
						spellcheck="false"
						disabled={!canManage}
						required
					/>
				</div>
				{#if canManage}
					<Button type="submit" disabled={pending.register}>Register domain</Button>
				{/if}
			</form>
			{#if canManage}
				<form method="POST" action="?/test" use:enhance={submitTest} class="pt-4">
					<Button type="submit" variant="outline" disabled={pending.test}>Send test email</Button>
				</form>
				<p class="text-muted-foreground pt-2 text-sm">
					Sends to your own address, using the sender this firm would use now.
				</p>
			{/if}
		</CardContent>
	</Card>
{:else}
	<div class="space-y-6">
		<Card>
			<CardHeader>
				<div class="flex flex-wrap items-center justify-between gap-3">
					<div class="space-y-1">
						<CardTitle>{domain.domain}</CardTitle>
						<CardDescription>
							{#if domain.active}
								Mail comes from {data.tenant.name} &lt;{domain.from_address}&gt;. Replies go to the
								firm's support address.
							{:else if domain.sending_verified}
								Sending is verified. Choose the address mail should come from before it is used.
							{:else}
								Mail still comes from Graphene. Add the DNS records below, then start verification.
							{/if}
						</CardDescription>
					</div>
					<StatusBadge tone={tone(domain)}>{label(domain)}</StatusBadge>
				</div>
			</CardHeader>
			<CardContent class="space-y-6">
				{#if domain.records.length === 0}
					<Alert>
						<AlertDescription
							>Resend has not returned DNS records for this domain yet.</AlertDescription
						>
					</Alert>
				{:else if recordsReady}
					<p class="text-sm">The DNS records are verified. There is nothing further to publish.</p>
					{#each domain.records.filter((record) => !record.required) as record (record.purpose + record.name)}
						<div class="space-y-2 rounded-lg border p-3">
							<div class="flex flex-wrap items-center gap-2">
								<p class="text-sm font-medium">{record.purpose}</p>
								<StatusBadge tone="neutral">Optional</StatusBadge>
							</div>
							<p class="text-muted-foreground text-sm">
								Publishing this does not affect whether mail can be sent. It tells other mail
								servers what to do with messages that fail SPF and DKIM.
							</p>
							<p class="font-mono text-xs break-all">
								{record.type}
								{record.name}
								<CopyButton value={record.name} label={`Copy ${record.purpose} name`} />
							</p>
							<p class="font-mono text-xs break-all">
								{record.value}
								<CopyButton value={record.value} label={`Copy ${record.purpose} value`} />
							</p>
						</div>
					{/each}
				{:else}
					<div class="overflow-x-auto rounded-lg border">
						<table class="w-full text-sm">
							<thead class="text-muted-foreground bg-muted/40">
								<tr>
									<th class="px-3 py-2 text-left font-medium">Purpose</th>
									<th class="px-3 py-2 text-left font-medium">Type</th>
									<th class="px-3 py-2 text-left font-medium">Name</th>
									<th class="px-3 py-2 text-left font-medium">Value</th>
									<th class="px-3 py-2 text-left font-medium">Status</th>
								</tr>
							</thead>
							<tbody>
								{#each domain.records as record, index (record.type + record.name + index)}
									<tr class="border-t align-top">
										<td class="px-3 py-2">
											{record.purpose || '—'}
											{#if !record.required}
												<span class="text-muted-foreground"> (optional)</span>
											{/if}
										</td>
										<td class="px-3 py-2 font-mono text-xs">{record.type}</td>
										<td class="px-3 py-2">
											<span class="inline-flex items-start gap-1 font-mono text-xs break-all">
												{record.name}
												<CopyButton
													value={record.name}
													label={`Copy ${record.purpose || record.type} name`}
												/>
											</span>
										</td>
										<td class="px-3 py-2">
											<span class="inline-flex items-start gap-1 font-mono text-xs break-all">
												{record.value}{record.priority != null
													? ` (priority ${record.priority})`
													: ''}
												<CopyButton
													value={record.value}
													label={`Copy ${record.purpose || record.type} value`}
												/>
											</span>
										</td>
										<td class="text-muted-foreground px-3 py-2">
											{record.required ? record.status : 'Optional'}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}

				{#if domain.records.length > 0}
					<Button
						type="button"
						variant="outline"
						onclick={() => downloadRecords(domain.domain, domain.records)}
					>
						Download DNS records
					</Button>
				{/if}

				{#if canManage}
					<div class="flex flex-wrap gap-2">
						{#if !recordsReady}
							<form
								method="POST"
								action="?/verify"
								use:enhance={submitWith('verify', 'Verification started.')}
							>
								<Button type="submit" disabled={pending.verify}>Start verification</Button>
							</form>
						{/if}
						<form
							method="POST"
							action="?/refresh"
							use:enhance={submitWith('refresh', 'Status refreshed.')}
						>
							<Button type="submit" variant="outline" disabled={pending.refresh}
								>Refresh status</Button
							>
						</form>
						<Button type="button" variant="outline" onclick={() => (removeOpen = true)}
							>Remove domain</Button
						>
					</div>
					{#if !recordsReady}
						<p class="text-muted-foreground text-sm">
							Start verification after the records are published. Refresh reads the current result
							without beginning another check. A new check marks a verified domain pending until it
							passes again.
						</p>
					{/if}
				{/if}
			</CardContent>
		</Card>

		<Card>
			<CardHeader>
				<CardTitle>From address</CardTitle>
				<CardDescription>
					Any mailbox on {domain.domain}. It does not have to exist as a real inbox. The firm's
					support address stays the reply-to.
				</CardDescription>
			</CardHeader>
			<CardContent>
				<form
					method="POST"
					action="?/setAddress"
					use:enhance={submitWith('setAddress', 'From address saved.')}
					class="space-y-4"
				>
					<div class="space-y-2">
						<Label for="from_address">Address</Label>
						<EmailInput
							id="from_address"
							name="from_address"
							value={fromAddress}
							onchange={(v) => (fromAddress = v)}
							placeholder={`notifications@${domain.domain}`}
							disabled={!canManage}
							required
						/>
					</div>
					{#if canManage}
						<Button type="submit" disabled={pending.setAddress}>Save address</Button>
					{/if}
				</form>
				{#if canManage}
					<form method="POST" action="?/test" use:enhance={submitTest} class="pt-4">
						<Button type="submit" variant="outline" disabled={pending.test}>Send test email</Button>
					</form>
					<p class="text-muted-foreground pt-2 text-sm">
						Sends to your own address, using the sender this firm would use now.
					</p>
				{/if}
			</CardContent>
		</Card>
	</div>

	<form
		method="POST"
		action="?/remove"
		bind:this={removeForm}
		use:enhance={submitWith('remove', 'Sending domain removed.')}
	></form>
	<ConfirmDialog
		bind:open={removeOpen}
		title="Remove this sending domain?"
		description="Mail from this firm goes back to the Graphene address. The DNS records can stay; they stop being used."
		confirmLabel="Remove domain"
		destructive
		onconfirm={() => removeForm?.requestSubmit()}
	/>
{/if}
