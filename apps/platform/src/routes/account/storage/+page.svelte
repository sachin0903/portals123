<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Badge,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import KeyIcon from '@lucide/svelte/icons/key-round';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import CheckIcon from '@lucide/svelte/icons/circle-check';
	import AlertTriangleIcon from '@lucide/svelte/icons/triangle-alert';

	let { data, form } = $props();

	let issuing = $state(false);
	let copied = $state<string | null>(null);

	const storage = $derived(data.storage);
	const issued = $derived(form?.issued ?? null);
	const credential = $derived(issued ?? storage?.credential ?? null);

	async function copy(label: string, value: string) {
		await navigator.clipboard.writeText(value);
		copied = label;
		setTimeout(() => (copied = null), 1500);
	}
</script>

<div class="space-y-6">
	{#if !data.authorised}
		<Alert>
			<AlertTriangleIcon class="size-4" />
			<AlertTitle>No storage access</AlertTitle>
			<AlertDescription>
				You cannot access the firm’s file storage. Ask an administrator if you need access.
			</AlertDescription>
		</Alert>
	{:else if !storage}
		<Alert>
			<AlertTriangleIcon class="size-4" />
			<AlertTitle>Storage not available</AlertTitle>
			<AlertDescription>
				The firm’s file storage is not ready. Ask an administrator for help.
			</AlertDescription>
		</Alert>
	{:else}
		<Card>
			<CardHeader>
				<CardTitle>Storage access</CardTitle>
				<CardDescription>
					Use these personal credentials to connect an S3-compatible file tool. Your access is
					limited to the folders shown below.
				</CardDescription>
			</CardHeader>
			<CardContent class="space-y-4">
				<dl class="grid gap-x-8 gap-y-2 text-xs sm:grid-cols-2">
					{#if storage.endpoint}
						<div>
							<dt class="text-muted-foreground text-xs">Endpoint</dt>
							<dd class="font-mono">{storage.endpoint}</dd>
						</div>
					{/if}
					<div>
						<dt class="text-muted-foreground text-xs">Bucket</dt>
						<dd class="font-mono">{storage.bucket}</dd>
					</div>
					<div>
						<dt class="text-muted-foreground text-xs">Readable</dt>
						<dd class="flex flex-wrap gap-1">
							{#each storage.read_prefixes as prefix (prefix)}
								<Badge variant="secondary" class="font-mono">{prefix}</Badge>
							{:else}
								<span class="text-muted-foreground">—</span>
							{/each}
						</dd>
					</div>
					<div>
						<dt class="text-muted-foreground text-xs">Writable</dt>
						<dd>
							{#if storage.shared}
								<Badge variant="secondary" class="font-mono">files/shared/</Badge>
							{:else}
								<span class="text-muted-foreground">read only</span>
							{/if}
						</dd>
					</div>
				</dl>
			</CardContent>
		</Card>

		{#if issued}
			<Alert>
				<KeyIcon class="size-4" />
				<AlertTitle>Credentials issued. Copy the secret now</AlertTitle>
				<AlertDescription>
					<p class="mb-3">
						This is the only time the secret is shown. Store it somewhere safe; if it's lost, rotate
						to get a new one.
					</p>
					<div class="space-y-2 font-mono text-sm">
						<div class="flex items-center gap-2">
							<span class="text-muted-foreground w-28 shrink-0">Access key</span>
							<code class="bg-muted rounded px-1.5 py-0.5">{issued.access_key_id}</code>
							<Button
								variant="ghost"
								size="icon"
								class="size-7"
								onclick={() => copy('key', issued.access_key_id)}
							>
								{#if copied === 'key'}<CheckIcon class="size-3.5" />{:else}<CopyIcon
										class="size-3.5"
									/>{/if}
							</Button>
						</div>
						<div class="flex items-center gap-2">
							<span class="text-muted-foreground w-28 shrink-0">Secret key</span>
							<code class="bg-muted rounded px-1.5 py-0.5 break-all"
								>{issued.secret_access_key}</code
							>
							<Button
								variant="ghost"
								size="icon"
								class="size-7"
								onclick={() => copy('secret', issued.secret_access_key)}
							>
								{#if copied === 'secret'}<CheckIcon class="size-3.5" />{:else}<CopyIcon
										class="size-3.5"
									/>{/if}
							</Button>
						</div>
					</div>
				</AlertDescription>
			</Alert>
		{/if}

		<Card>
			<CardHeader>
				<CardTitle>Your credentials</CardTitle>
				<CardDescription>
					You can have one active credential. Issuing another replaces it immediately.
				</CardDescription>
			</CardHeader>
			<CardContent class="space-y-4">
				{#if form?.error}
					<Alert variant="destructive">
						<AlertDescription>{form.error}</AlertDescription>
					</Alert>
				{/if}
				{#if credential}
					<div class="flex flex-wrap items-center gap-3 text-sm">
						<KeyIcon class="text-muted-foreground size-4" />
						<code class="bg-muted rounded px-1.5 py-0.5 font-mono">{credential.access_key_id}</code>
						<Badge variant="secondary">active</Badge>
					</div>
				{:else}
					<p class="text-muted-foreground text-sm">No active credential.</p>
				{/if}
				{#if data.canIssue && storage.credentials_supported}
					<div class="flex gap-2">
						<form
							method="POST"
							action="?/issue"
							use:enhance={() => {
								issuing = true;
								return async ({ update }) => {
									issuing = false;
									await update({ reset: false });
								};
							}}
						>
							<Button type="submit" disabled={issuing}>
								{credential ? 'Rotate credentials' : 'Issue credentials'}
							</Button>
						</form>
						{#if credential && !issued}
							<form method="POST" action="?/revoke" use:enhance>
								<input type="hidden" name="uid" value={credential.uid} />
								<Button type="submit" variant="outline">Revoke</Button>
							</form>
						{/if}
					</div>
				{:else if !storage.credentials_supported}
					<p class="text-muted-foreground text-sm">
						Personal credentials are unavailable. Ask an administrator for another way to access the
						files.
					</p>
				{/if}
			</CardContent>
		</Card>
	{/if}
</div>
