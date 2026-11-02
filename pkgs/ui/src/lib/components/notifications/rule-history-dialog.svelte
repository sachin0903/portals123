<script lang="ts">
	// A rule's change history — the notification-rule equivalent of the email version-history dialog.
	// Fetches notifications.rule_versions (newest first) from an injected URL, and shows for each
	// change WHO made it (Graphene vs the firm), WHEN, and WHAT changed vs the previous version. Read
	// only — this is an audit, not a restore surface.
	import * as Dialog from '../ui/dialog/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { relativeTime } from './describe.js';
	import { now } from './now.svelte.js';
	import type { RuleVersion } from './rule-types.js';

	let {
		open = $bindable(false),
		url = null,
		ruleLabel = ''
	}: { open?: boolean; url?: string | null; ruleLabel?: string } = $props();

	let versions = $state<RuleVersion[]>([]);
	let loading = $state(false);
	let error = $state(false);

	// Fetch when opened (client-only: the effect doesn't run in SSR, and is gated on `open`).
	$effect(() => {
		if (!open || !url) return;
		const target = url;
		loading = true;
		error = false;
		versions = [];
		fetch(target)
			.then((r) => (r.ok ? (r.json() as Promise<RuleVersion[]>) : Promise.reject()))
			.then((rows) => (versions = rows))
			.catch(() => (error = true))
			.finally(() => (loading = false));
	});

	const ACTION_LABEL: Record<string, string> = {
		created: 'Created',
		updated: 'Updated',
		deleted: 'Deleted'
	};
	const who = (scope: string) => (scope === 'operations' ? 'Graphene' : 'Your firm');

	// The fields that differ between a version and the one before it (older) — a compact "what
	// changed" summary. The oldest version (the create) has no predecessor, so it lists nothing.
	function changes(v: RuleVersion, prev: RuleVersion | undefined): string[] {
		if (!prev) return [];
		const out: string[] = [];
		if (v.title !== prev.title) out.push('title');
		if ((v.body ?? '') !== (prev.body ?? '')) out.push('message');
		if ((v.link ?? '') !== (prev.link ?? '')) out.push('link');
		if (v.default_in_app !== prev.default_in_app) out.push('in-app');
		if (v.default_email !== prev.default_email) out.push('email');
		if (v.mandatory !== prev.mandatory) out.push('mandatory');
		if (v.enabled !== prev.enabled) out.push('enabled');
		if ((v.email_code ?? '') !== (prev.email_code ?? '')) out.push('template');
		if (v.priority !== prev.priority) out.push('priority');
		return out;
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Change history</Dialog.Title>
			<Dialog.Description>
				{ruleLabel ? `${ruleLabel} — who changed it, and what.` : 'Who changed this, and what.'}
			</Dialog.Description>
		</Dialog.Header>
		{#if loading}
			<p class="text-muted-foreground text-sm">Loading…</p>
		{:else if error}
			<p class="text-destructive text-sm">Couldn't load the history. Please try again.</p>
		{:else if !versions.length}
			<p class="text-muted-foreground text-sm">No changes recorded yet.</p>
		{:else}
			<ol class="max-h-[60vh] space-y-3 overflow-y-auto">
				{#each versions as v, i (v.uid)}
					{@const changed = changes(v, versions[i + 1])}
					<li class="rounded-lg border px-3 py-2.5">
						<div class="flex items-center gap-2">
							<span class="text-sm font-medium">{ACTION_LABEL[v.action] ?? v.action}</span>
							<Badge
								variant={v.authored_by_scope === 'operations' ? 'secondary' : 'outline'}
								class="font-normal"
							>
								{who(v.authored_by_scope)}
							</Badge>
							<span class="text-muted-foreground ml-auto text-xs"
								>{relativeTime(v.created_at, now())}</span
							>
						</div>
						<p class="mt-1 truncate text-sm">{v.title}</p>
						{#if changed.length}
							<p class="text-muted-foreground mt-1 text-xs">Changed: {changed.join(', ')}</p>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}
	</Dialog.Content>
</Dialog.Root>
