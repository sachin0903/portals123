<script lang="ts">
	/**
	 * Edit a template's name/code/description after creation (host PATCH; duplicate code → inline
	 * error). No category field: brand comes from `audience`, and since the PATCH is partial
	 * (`COALESCE(:cat, category)`), a defaulted one here would silently reset it. `enhance`/`onSaved` are app-injected — pkgs/ui cannot import `$app/forms`.
	 */
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import { Button } from '../ui/button/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import type { Enhance } from '../book/clients/form-enhance.js';

	let {
		open = $bindable(false),
		template,
		enhance,
		updateAction = '?/update',
		onSaved
	}: {
		open?: boolean;
		template: {
			uid: string;
			name: string;
			code: string;
			description: string | null;
		} | null;
		enhance: Enhance;
		updateAction?: string;
		onSaved?: () => void | Promise<void>;
	} = $props();

	let name = $state('');
	let code = $state('');
	let description = $state('');
	let errorMsg = $state<string | null>(null);
	let saving = $state(false);

	// Seed the fields on open; reset the seed marker on close so reopening reseeds from the record.
	let seededUid = $state<string | null>(null);
	$effect(() => {
		if (!open) {
			seededUid = null;
			return;
		}
		const t = template;
		if (!t || t.uid === seededUid) return;
		seededUid = t.uid;
		name = t.name;
		code = t.code;
		description = t.description ?? '';
		errorMsg = null;
	});
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Template settings</Dialog.Title>
			<Dialog.Description>Rename or re-code this email template.</Dialog.Description>
		</Dialog.Header>

		{#if errorMsg}
			<Alert variant="destructive"><AlertDescription>{errorMsg}</AlertDescription></Alert>
		{/if}

		<form
			method="POST"
			action={updateAction}
			use:enhance={() => {
				saving = true;
				errorMsg = null;
				return async ({ result, update }) => {
					if (result.type === 'success') {
						open = false;
						await onSaved?.();
					} else if (result.type === 'failure') {
						errorMsg = String(result.data?.error ?? 'Update failed');
					} else {
						await update();
					}
					saving = false;
				};
			}}
			class="space-y-4"
		>
			<input type="hidden" name="uid" value={template?.uid ?? ''} />
			<div class="grid gap-1.5">
				<Label for="ts-name">Name</Label>
				<Input id="ts-name" name="name" bind:value={name} />
			</div>
			<div class="grid gap-1.5">
				<Label for="ts-code">Code</Label>
				<Input id="ts-code" name="code" bind:value={code} />
				<p class="text-muted-foreground text-xs">
					The stable key the send path resolves (e.g. <code>invite</code>).
				</p>
			</div>
			<div class="grid gap-1.5">
				<Label for="ts-desc">Description</Label>
				<Textarea id="ts-desc" name="description" bind:value={description} rows={2} />
			</div>

			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (open = false)}>Cancel</Button>
				<Button type="submit" disabled={saving || !name.trim() || !code.trim()}>
					{saving ? 'Saving…' : 'Save'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
