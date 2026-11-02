<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		humanise,
		Input,
		Label,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import EyeOffIcon from '@lucide/svelte/icons/eye-off';
	import type { SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const env = $derived(data.environment);

	// Onboarding checklist for the manual WorkOS setup (portal-via-application). Each item auto-ticks
	// off real stored state, so an operator can see at a glance what a new firm still needs. The
	// Internal Operations application is Graphene-only, so it is a form field but not a checklist item.
	const setupChecklist = $derived([
		{ label: 'WorkOS project registered', done: !!env?.workos_client_id },
		{ label: 'Client portal host set', done: !!env?.client_portal_host },
		{ label: 'Platform host set', done: !!env?.platform_host },
		{ label: 'API key & cookie password set', done: !!env?.has_secrets },
		{ label: 'Firm org created', done: !!env?.firm_org_id },
		{
			label: 'Client Portal application configured',
			done: !!env?.client_portal_client_id && !!env?.has_client_portal_secret
		},
		{
			label: 'Platform application configured',
			done: !!env?.platform_client_id && !!env?.has_platform_secret
		}
	]);
	const setupDone = $derived(setupChecklist.filter((i) => i.done).length);

	// Status labels read Title-case everywhere in the product, including the raw stage string.

	// Cookie password field (bound so Generate can fill it), with a reveal toggle + copy.
	let cookiePw = $state('');
	let revealPw = $state(false);

	/** A secure random 64-char hex secret for sealing session cookies (>= 32 chars). */
	function generatePassword(): string {
		const bytes = crypto.getRandomValues(new Uint8Array(32));
		return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
	}

	function copyPassword() {
		if (!cookiePw) return;
		navigator.clipboard?.writeText(cookiePw);
		toast.success('Cookie password copied to the clipboard.');
	}

	// Per-form pending flags + a shared enhance callback that reports outcomes via toast.
	let pending = $state<Record<string, boolean>>({});
	function submitWith(key: string, success: string): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					toast.success(success);
					await update({ reset: false });
				} else {
					toast.error(
						result.type === 'failure'
							? String(result.data?.error ?? 'Something went wrong.')
							: 'Something went wrong.'
					);
					await update();
				}
			};
		};
	}

	// Saving keys over existing ones is a rotation — confirm before breaking live sessions.
	let secretsForm = $state<HTMLFormElement | null>(null);
	let rotateOpen = $state(false);
</script>

<!-- The heading is the Settings layout's; this panel is its Sign-in section. -->
<Card glass>
	<CardHeader>
		<div class="flex flex-wrap items-start justify-between gap-2">
			<CardTitle>WorkOS configuration</CardTitle>
			{#if env}
				<StatusBadge tone="neutral">{humanise(env.stage)}</StatusBadge>
			{/if}
		</div>
		<CardDescription>
			Create a WorkOS project for this firm, then add its portal hosts and credentials here. Keys
			are encrypted and cannot be viewed after saving.
		</CardDescription>
	</CardHeader>
	<CardContent class="space-y-4">
		<!-- Routing config (prefilled → editing preserves the other fields; blank would wipe them) -->
		<form
			method="POST"
			action="?/saveConfiguration"
			use:enhance={submitWith('config', 'Configuration saved.')}
			class="grid gap-3 sm:grid-cols-2"
		>
			<div class="space-y-1">
				<Label for="workos_client_id">WorkOS client ID</Label>
				<Input
					id="workos_client_id"
					name="workos_client_id"
					value={env?.workos_client_id ?? ''}
					placeholder="client_…"
					disabled={!data.canManage}
				/>
			</div>
			<div class="space-y-1">
				<Label for="issuer">
					Token issuer <span class="text-muted-foreground">(optional)</span>
				</Label>
				<Input
					id="issuer"
					name="issuer"
					value={env?.issuer ?? ''}
					placeholder="https://…"
					disabled={!data.canManage}
				/>
			</div>
			<div class="space-y-1">
				<Label for="client_portal_host">Client portal host</Label>
				<Input
					id="client_portal_host"
					name="client_portal_host"
					value={env?.client_portal_host ?? ''}
					placeholder="clients.firm.com"
					disabled={!data.canManage}
				/>
			</div>
			<div class="space-y-1">
				<Label for="platform_host">Platform host</Label>
				<Input
					id="platform_host"
					name="platform_host"
					value={env?.platform_host ?? ''}
					placeholder="app.firm.com"
					disabled={!data.canManage}
				/>
			</div>
			<div class="space-y-1">
				<Label for="operations_host">
					Operations host <span class="text-muted-foreground">(Graphene only)</span>
				</Label>
				<Input
					id="operations_host"
					name="operations_host"
					value={env?.operations_host ?? ''}
					placeholder="ops.grapheneplatforms.com"
					disabled={!data.canManage}
				/>
			</div>
			{#if data.canManage}
				<div class="flex items-center gap-3 sm:col-span-2">
					<Button type="submit" size="sm" disabled={pending.config}>
						{pending.config ? 'Saving…' : 'Save configuration'}
					</Button>
				</div>
			{/if}
		</form>

		<!-- Keys (encrypted at rest; never echoed back) — only once the config row exists -->
		{#if env}
			<div class="flex items-center gap-2 border-t pt-3">
				<StatusBadge tone={env.has_secrets ? 'success' : 'danger'}>
					{env.has_secrets ? 'Keys set' : 'Keys missing'}
				</StatusBadge>
				<StatusBadge tone={env.firm_org_id ? 'success' : 'neutral'}>
					{env.firm_org_id ? 'Firm org created' : 'Firm org pending'}
				</StatusBadge>
			</div>
			<form
				method="POST"
				action="?/setSecrets"
				use:enhance={submitWith('secrets', 'Keys saved. The firm organisation is ready in WorkOS.')}
				bind:this={secretsForm}
				class="space-y-2"
			>
				<input type="hidden" name="environment_uid" value={env.uid} />
				<div class="text-muted-foreground text-xs font-medium">Set / rotate keys</div>
				<div class="grid gap-2 sm:grid-cols-2">
					<Input
						name="workos_api_key"
						type="password"
						placeholder="WorkOS API key"
						autocomplete="off"
						disabled={!data.canManage}
					/>
					<div class="flex items-center gap-2">
						<Input
							name="cookie_password"
							type={revealPw ? 'text' : 'password'}
							placeholder="Cookie password (≥ 32 chars)"
							autocomplete="off"
							bind:value={cookiePw}
							class="flex-1"
							disabled={!data.canManage}
						/>
						<Button
							type="button"
							variant="ghost"
							size="icon-sm"
							aria-label={revealPw ? 'Hide cookie password' : 'Reveal cookie password'}
							onclick={() => (revealPw = !revealPw)}
						>
							{#if revealPw}<EyeOffIcon />{:else}<EyeIcon />{/if}
						</Button>
						<Button
							type="button"
							variant="ghost"
							size="icon-sm"
							aria-label="Copy cookie password"
							disabled={!cookiePw}
							onclick={copyPassword}
						>
							<CopyIcon />
						</Button>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							disabled={!data.canManage}
							onclick={() => {
								cookiePw = generatePassword();
								toast.success('Cookie password generated. Copy it before saving.');
							}}
						>
							Generate
						</Button>
					</div>
				</div>
				{#if data.canManage}
					<div class="flex items-center gap-3">
						{#if env.has_secrets}
							<Button
								type="button"
								variant="secondary"
								size="sm"
								disabled={pending.secrets}
								onclick={() => (rotateOpen = true)}
							>
								{pending.secrets ? 'Saving…' : 'Save keys'}
							</Button>
						{:else}
							<Button type="submit" variant="secondary" size="sm" disabled={pending.secrets}>
								{pending.secrets ? 'Saving…' : 'Save keys'}
							</Button>
						{/if}
					</div>
				{/if}
			</form>
		{:else}
			<p class="text-muted-foreground border-t pt-3 text-sm">
				Save the configuration above before setting keys.
			</p>
		{/if}
	</CardContent>
</Card>

<Card glass class="mt-4">
	<CardHeader>
		<CardDescription>
			<span class="text-foreground font-medium">Portal applications.</span>
			Create a WorkOS application for each portal, set its redirect URIs and session policy, then add
			its client ID below.
		</CardDescription>
	</CardHeader>
	<CardContent class="space-y-4">
		{#if env}
			<!-- Setup checklist: auto-ticked from stored state, so nothing gets missed during onboarding. -->
			<div class="space-y-2">
				<div class="text-muted-foreground text-xs font-medium">
					Setup checklist: {setupDone}/{setupChecklist.length}
				</div>
				<ul class="grid gap-1.5 sm:grid-cols-2">
					{#each setupChecklist as item (item.label)}
						<li class="flex items-center gap-2 text-sm">
							<StatusBadge tone={item.done ? 'success' : 'neutral'}>
								{item.done ? 'Done' : 'To do'}
							</StatusBadge>
							<span class={item.done ? '' : 'text-muted-foreground'}>{item.label}</span>
						</li>
					{/each}
				</ul>
			</div>

			<form
				method="POST"
				action="?/saveApplications"
				use:enhance={submitWith('applications', 'Portal application credentials saved.')}
				class="grid gap-3 border-t pt-3 sm:grid-cols-2"
			>
				<div class="space-y-1">
					<Label for="client_portal_client_id">Client Portal app client ID</Label>
					<Input
						id="client_portal_client_id"
						name="client_portal_client_id"
						value={env.client_portal_client_id ?? ''}
						placeholder="client_…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="space-y-1">
					<Label for="client_portal_secret">
						Client Portal app secret
						{#if env.has_client_portal_secret}<span class="text-muted-foreground">(set)</span>{/if}
					</Label>
					<Input
						id="client_portal_secret"
						name="client_portal_secret"
						type="password"
						autocomplete="off"
						placeholder="sk_…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="space-y-1">
					<Label for="platform_client_id">Platform app client ID</Label>
					<Input
						id="platform_client_id"
						name="platform_client_id"
						value={env.platform_client_id ?? ''}
						placeholder="client_…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="space-y-1">
					<Label for="platform_secret">
						Platform app secret
						{#if env.has_platform_secret}<span class="text-muted-foreground">(set)</span>{/if}
					</Label>
					<Input
						id="platform_secret"
						name="platform_secret"
						type="password"
						autocomplete="off"
						placeholder="sk_…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="space-y-1">
					<Label for="operations_client_id">
						Internal Operations app client ID
						<span class="text-muted-foreground">(Graphene only)</span>
					</Label>
					<Input
						id="operations_client_id"
						name="operations_client_id"
						value={env.operations_client_id ?? ''}
						placeholder="client_…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="space-y-1">
					<Label for="operations_secret">
						Internal Operations app secret
						{#if env.has_operations_secret}<span class="text-muted-foreground">(set)</span>{/if}
					</Label>
					<Input
						id="operations_secret"
						name="operations_secret"
						type="password"
						autocomplete="off"
						placeholder="sk_…"
						disabled={!data.canManage}
					/>
				</div>
				{#if data.canManage}
					<div class="sm:col-span-2">
						<Button type="submit" size="sm" disabled={pending.applications}>
							{pending.applications ? 'Saving…' : 'Save application credentials'}
						</Button>
					</div>
				{/if}
			</form>
		{:else}
			<p class="text-muted-foreground text-sm">
				Register the WorkOS configuration above before recording portal applications.
			</p>
		{/if}
	</CardContent>
</Card>

<ConfirmDialog
	bind:open={rotateOpen}
	title="Rotate the WorkOS keys?"
	description="Existing sessions for this firm will break."
	confirmLabel="Rotate keys"
	destructive
	onconfirm={() => secretsForm?.requestSubmit()}
/>
