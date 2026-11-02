<script lang="ts">
	import type { FormField, FormScoring } from '@graphene/ui';
	import { Button, ConfirmDialog, StatusBadge, toast, FORM_STATUS_META } from '@graphene/ui';
	import { deserialize } from '$app/forms';
	import { beforeNavigate, goto } from '$app/navigation';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import BackButton from '$lib/components/back-button.svelte';
	import CheckIcon from '@lucide/svelte/icons/check';
	import { FormBuilder } from '@graphene/ui/form-builder';
	import { toFieldPayload } from '$lib/forms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const form = $derived(data.form);
	const initialFields = $derived(data.fields);

	// Explicit save: the builder reports every change (incl. per-keystroke section renames), but
	// nothing persists until Save is pressed. Navigating away with unsaved changes asks first.
	let latest: FormField[] | null = null;
	let scoringLatest: FormScoring | null | undefined = undefined;
	let dirty = $state(false);
	let saveState = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');

	function markDirty() {
		dirty = true;
		if (saveState !== 'idle') saveState = 'idle';
	}

	function onBuilderChange(fields: FormField[]) {
		latest = fields;
		markDirty();
	}

	function onScoringChange(scoring: FormScoring | null) {
		scoringLatest = scoring;
		markDirty();
	}

	async function save() {
		if (!dirty || saveState === 'saving') return;
		saveState = 'saving';
		const body = new FormData();
		body.append('fields', JSON.stringify((latest ?? initialFields).map(toFieldPayload)));
		if (scoringLatest !== undefined) body.append('scoring', JSON.stringify(scoringLatest));
		try {
			const res = await fetch('?/saveFields', {
				method: 'POST',
				body,
				headers: { 'x-sveltekit-action': 'true' }
			});
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				dirty = false;
				saveState = 'saved';
			} else {
				saveState = 'error';
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Could not save the form.')
						: 'Could not save the form.'
				);
			}
		} catch {
			saveState = 'error';
			toast.error('Could not save the form.');
		}
	}

	// Unsaved-changes guard: cancel the navigation, ask via ConfirmDialog, and replay it on Leave.
	let leaveOpen = $state(false);
	let pendingHref: string | null = null;
	let leaveConfirmed = false;

	beforeNavigate((nav) => {
		if (!dirty || leaveConfirmed) return;
		nav.cancel();
		pendingHref = nav.to?.url.href ?? null;
		leaveOpen = true;
	});

	const statusLine = $derived(
		saveState === 'saving'
			? 'Saving…'
			: saveState === 'error'
				? 'Save failed. Try again.'
				: dirty
					? 'Unsaved changes.'
					: saveState === 'saved'
						? 'Draft saved.'
						: 'Changes are kept until you save.'
	);
</script>

<svelte:head><title>{form ? `Edit · ${form.name}` : 'Edit form'} · Platform</title></svelte:head>

{#if !form}
	<div class="mx-auto max-w-3xl">
		<p class="text-muted-foreground text-sm">This form could not be found.</p>
		<Button variant="outline" class="mt-4" href="/forms">
			<ArrowLeftIcon class="mr-1.5 size-4" /> Back to forms
		</Button>
	</div>
{:else}
	{@const meta = FORM_STATUS_META[form.status]}
	<div class="space-y-6">
		<div class="flex items-start justify-between gap-4">
			<div>
				<BackButton fallback={`/forms/${form.uid}`} fallbackLabel="form" />
				<div class="mt-2 flex items-center gap-2">
					<h1 class="text-2xl font-semibold">{form.name}</h1>
					<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
				</div>
				<p class="text-muted-foreground mt-1 font-mono text-sm">{form.code}</p>
				<p
					class="mt-1 text-sm"
					class:text-muted-foreground={!dirty && saveState !== 'error'}
					class:text-warning={dirty && saveState !== 'error'}
					class:text-destructive={saveState === 'error'}
				>
					{statusLine}
				</p>
			</div>
			<div class="flex shrink-0 items-center gap-2">
				<Button variant="outline" href={`/forms/${form.uid}`}>Done</Button>
				<Button onclick={save} disabled={!dirty || saveState === 'saving'}>
					<CheckIcon class="mr-1.5 size-4" />
					{saveState === 'saving' ? 'Saving…' : 'Save'}
				</Button>
			</div>
		</div>

		<FormBuilder
			fields={initialFields}
			scoring={form.scoring ?? null}
			onchange={onBuilderChange}
			onscoring={onScoringChange}
		/>
	</div>
{/if}

<ConfirmDialog
	bind:open={leaveOpen}
	title="Discard unsaved changes?"
	description="You have unsaved changes. Leave without saving?"
	confirmLabel="Leave"
	destructive
	onconfirm={() => {
		leaveConfirmed = true;
		if (pendingHref) goto(pendingHref);
	}}
/>
