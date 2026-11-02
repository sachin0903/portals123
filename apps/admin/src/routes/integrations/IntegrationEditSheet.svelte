<script lang="ts">
	// The approve/reject/configure form, shared by the list's row-click Sheet and the detail page's
	// header "Edit" — same fields, same rules, different routes to post to (the list's action is
	// `?/update`, the detail page's is `?/updateIntegration`), hence `formAction`.
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Button,
		Label,
		Segmented,
		SelectMenu,
		Sheet,
		Switch,
		Textarea,
		toast
	} from '@graphene/ui';
	import type { CodeRef, IntegrationSummary, TenantRef } from '$lib/api';

	let {
		open = $bindable(false),
		integration,
		firms,
		features,
		canWrite,
		formAction,
		onSaved
	}: {
		open?: boolean;
		integration: IntegrationSummary | null;
		firms: TenantRef[];
		features: CodeRef[];
		canWrite: boolean;
		formAction: string;
		onSaved: () => void;
	} = $props();

	let firmId = $state('');
	let featureSet = $state<Set<string>>(new Set());
	// Plain `string`, not `IntegrationStatus`: `Segmented`'s bindable `value` is a plain string, and
	// a narrower bound type there is a two-way binding mismatch under strict mode.
	let status = $state('pending');
	let enabled = $state(false);
	let reason = $state('');
	let saving = $state(false);
	let saveError = $state<string | null>(null);

	const firmOptions = $derived(firms.map((t) => ({ value: String(t.id), label: t.name })));
	// Ported as-is from the legacy's `canSave`: a rejection with no stated reason cannot be saved.
	const canSave = $derived(!(status === 'rejected' && reason.trim() === ''));

	const statusOptions = [
		{ value: 'pending', label: 'Pending' },
		{ value: 'approved', label: 'Approved' },
		{ value: 'rejected', label: 'Rejected' }
	] as const;

	function toggleFeature(code: string, on: boolean) {
		const next = new Set(featureSet);
		if (on) next.add(code);
		else next.delete(code);
		featureSet = next;
	}

	// Re-seed every field when a (different) integration opens — not on every render, which would
	// stamp over whatever the operator is mid-editing.
	let seededFor: string | null = null;
	$effect(() => {
		if (!open || !integration || seededFor === integration.uid) return;
		seededFor = integration.uid;
		firmId = integration.firm_id != null ? String(integration.firm_id) : '';
		featureSet = new Set(integration.feature_ids);
		status = integration.status;
		enabled = integration.enabled;
		reason = integration.rejection_reason ?? '';
		saveError = null;
	});
	$effect(() => {
		if (!open) seededFor = null;
	});
</script>

<Sheet.Root bind:open>
	<Sheet.Content class="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
		{#if integration}
			<Sheet.Header class="shrink-0 border-b px-6 pt-6 pb-4">
				<Sheet.Title>{integration.platform ?? 'Integration'}</Sheet.Title>
				<Sheet.Description>{integration.tenant_name}</Sheet.Description>
			</Sheet.Header>

			<form
				method="POST"
				action={formAction}
				use:enhance={() => {
					saving = true;
					return async ({ result, update }) => {
						saving = false;
						if (result.type === 'success') {
							toast.success('Integration updated.');
							saveError = null;
							open = false;
							onSaved();
						} else {
							saveError =
								result.type === 'failure'
									? String(result.data?.error ?? 'Something went wrong.')
									: 'Something went wrong.';
						}
						await update();
					};
				}}
				class="flex min-h-0 flex-1 flex-col"
			>
				<input type="hidden" name="uid" value={integration.uid} />

				<div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
					{#if saveError}
						<Alert variant="destructive">
							<AlertDescription>{saveError}</AlertDescription>
						</Alert>
					{/if}

					<div class="flex items-center gap-2">
						<Switch id="enabled" name="enabled" bind:checked={enabled} disabled={!canWrite} />
						<Label for="enabled" class="font-normal">Enabled</Label>
					</div>

					<div class="space-y-1.5">
						<Label for="firm_id">Firm</Label>
						<SelectMenu
							id="firm_id"
							name="firm_id"
							items={firmOptions}
							value={firmId}
							onValueChange={(next) => (firmId = next)}
							placeholder="Choose a firm…"
							portal={false}
							disabled={!canWrite}
						/>
					</div>

					<div class="space-y-1.5">
						<Label>Features</Label>
						<div class="space-y-2 rounded-md border p-3">
							{#each features as feature (feature.code)}
								<div class="flex items-center justify-between gap-2">
									<Label for={`feature-${feature.code}`} class="font-normal">
										{feature.description || feature.code}
									</Label>
									<Switch
										id={`feature-${feature.code}`}
										checked={featureSet.has(feature.code)}
										onCheckedChange={(next) => toggleFeature(feature.code, next)}
										disabled={!canWrite}
									/>
								</div>
							{:else}
								<p class="text-muted-foreground text-sm">No features defined.</p>
							{/each}
						</div>
						{#each [...featureSet] as code (code)}
							<input type="hidden" name="features" value={code} />
						{/each}
					</div>

					<div class="space-y-1.5">
						<Label>Status</Label>
						<Segmented options={statusOptions} bind:value={status} disabled={!canWrite} />
						<input type="hidden" name="status" value={status} />
					</div>

					<div class="space-y-1.5">
						<Label for="reason">
							Reason
							{#if status === 'rejected'}<span class="text-destructive">*</span>{/if}
						</Label>
						<Textarea
							id="reason"
							name="reason"
							bind:value={reason}
							placeholder={status === 'rejected'
								? 'Required — why this integration is being rejected.'
								: 'Optional note.'}
							disabled={!canWrite}
						/>
						{#if status === 'rejected' && reason.trim() === ''}
							<p class="text-destructive text-xs">A reason is required to reject an integration.</p>
						{/if}
					</div>
				</div>

				<div class="flex shrink-0 justify-end gap-2 border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (open = false)}>Close</Button>
					{#if canWrite}
						<Button type="submit" disabled={saving || !canSave}>
							{saving ? 'Saving…' : 'Save'}
						</Button>
					{/if}
				</div>
			</form>
		{/if}
	</Sheet.Content>
</Sheet.Root>
