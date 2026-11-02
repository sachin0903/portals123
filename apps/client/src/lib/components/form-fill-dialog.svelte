<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		Dialog,
		DynamicForm,
		type FormField
	} from '@graphene/ui';

	let {
		open = $bindable(false),
		formName,
		fields = [],
		answers = {},
		dataSources = {},
		requestTitle,
		note,
		onsubmit,
		onsave
	}: {
		open?: boolean;
		formName?: string;
		/** The builder form's fields (shared schema). */
		fields?: FormField[];
		/** Answers already saved, so a return visit continues. */
		answers?: Record<string, unknown>;
		dataSources?: Record<string, unknown[]>;
		requestTitle?: string;
		note?: string | null;
		/** Called with the collected answers on a valid submit. */
		onsubmit: (values: Record<string, unknown>) => void | Promise<void>;
		/** Save without validating or closing. Absent hides the button. */
		onsave?: (values: Record<string, unknown>) => void | Promise<void>;
	} = $props();

	let values = $state<Record<string, unknown>>({});
	let submitting = $state(false);
	let saving = $state(false);
	// Bound to the DynamicForm instance to drive validate()/collect() from the dialog footer.
	let formRef = $state<{ validate: () => boolean; collect: () => Record<string, unknown> }>();

	$effect(() => {
		if (open) values = { ...answers };
	});

	async function saveProgress(): Promise<void> {
		if (saving || submitting || !formRef || !onsave) return;
		saving = true;
		try {
			await onsave(formRef.collect());
		} finally {
			saving = false;
		}
	}

	async function submit(): Promise<void> {
		if (submitting || !formRef) return;
		if (!formRef.validate()) return;
		submitting = true;
		try {
			await onsubmit(formRef.collect());
			open = false;
		} finally {
			submitting = false;
		}
	}
</script>

<Dialog.Root bind:open onOpenChange={(o) => !o && (values = {})}>
	<!-- A fact find is a document, not a confirmation. The stock dialog is 24rem. On a desktop the
	     panel is wide and the columns follow the form. On a phone it is the screen: a sheet is full
	     width below sm, and a card floating over the journey spends the gutter twice. -->
	<Dialog.Content
		class="flex max-h-[92svh] w-[min(72rem,calc(100%-1.5rem))] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none max-sm:!inset-0 max-sm:!h-dvh max-sm:!max-h-none max-sm:!w-full max-sm:!max-w-none max-sm:!translate-none max-sm:!rounded-none max-sm:ring-0"
	>
		<Dialog.Header
			class="shrink-0 px-4 pt-[max(1.25rem,env(safe-area-inset-top))] pr-14 pb-4 sm:px-6 sm:pt-5"
		>
			<Dialog.Title class="text-lg">{formName ?? 'Complete form'}</Dialog.Title>
			{#if requestTitle && requestTitle !== formName}
				<Dialog.Description>{requestTitle}</Dialog.Description>
			{/if}
		</Dialog.Header>

		{#if note}
			<div class="px-6 pb-2">
				<Alert class="bg-muted/40">
					<AlertTitle>Note from your adviser</AlertTitle>
					<AlertDescription>{note}</AlertDescription>
				</Alert>
			</div>
		{/if}

		<div
			class="min-h-0 flex-1 overflow-y-auto px-4 py-2 [--form-nav-bleed:1rem] sm:px-6 sm:[--form-nav-bleed:1.5rem]"
			style="--form-rail-max: calc(92svh - 11rem)"
		>
			{#if fields.length}
				{#key `${open}:${fields.map((field) => field.uid).join('|')}`}
					<DynamicForm
						bind:this={formRef}
						{fields}
						bind:values
						{dataSources}
						sectionNav
						showSubmit={false}
					/>
				{/key}
			{:else}
				<p class="text-muted-foreground py-6 text-center text-sm">
					This form has no questions yet.
				</p>
			{/if}
		</div>

		<!-- One row. Stacking Cancel, Save and Submit made a 44px-tall wall and left four fields on
		     screen. Close is the way out on a phone; Cancel returns beside them from sm. -->
		<Dialog.Footer
			class="mx-0 mb-0 shrink-0 flex-row items-center justify-end rounded-none px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4"
		>
			<Button variant="ghost" class="max-sm:hidden" onclick={() => (open = false)}>Cancel</Button>
			{#if onsave}
				<Button
					variant="outline"
					class="max-sm:flex-1"
					disabled={saving || submitting || !fields.length}
					onclick={saveProgress}
				>
					{saving ? 'Saving…' : 'Save progress'}
				</Button>
			{/if}
			<Button
				class="max-sm:flex-1"
				disabled={submitting || saving || !fields.length}
				onclick={submit}
			>
				{submitting ? 'Submitting…' : 'Submit form'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
