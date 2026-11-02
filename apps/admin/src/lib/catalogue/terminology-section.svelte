<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Input,
		toast
	} from '@graphene/ui';
	import RotateIcon from '@lucide/svelte/icons/rotate-ccw';
	import type { Term } from './types';

	let {
		terms,
		canManage,
		form
	}: {
		terms: Term[];
		canManage: boolean;
		form: { error?: string; saved?: string } | null;
	} = $props();

	// Grouped by category so the list reads as a vocabulary rather than 17 unrelated inputs.
	const groups = $derived.by(() => {
		const byCategory = new Map<string, Term[]>();
		for (const term of terms) {
			const existing = byCategory.get(term.category) ?? [];
			existing.push(term);
			byCategory.set(term.category, existing);
		}
		return [...byCategory.entries()];
	});

	const overridden = $derived(terms.filter((term) => term.overridden).length);

	$effect(() => {
		if (form?.saved === 'term') toast.success('Terminology saved');
		else if (form?.error) toast.error(form.error);
	});
</script>

<Card glass>
	<CardHeader>
		<CardTitle>Terminology</CardTitle>
		<CardDescription>
			Personalisation, not translation: a firm that says “instruments” where the platform says
			“assets” shouldn't read the wrong word on every page. {overridden} of {terms.length} overridden.
			Leave a field blank to fall back to the default.
		</CardDescription>
	</CardHeader>
	<CardContent class="space-y-6">
		{#each groups as [category, rows] (category)}
			<section class="space-y-2">
				<h3 class="text-muted-foreground text-xs font-semibold">
					{category}
				</h3>
				<div class="space-y-2">
					{#each rows as term (term.key)}
						<form
							method="POST"
							action="?/term"
							use:enhance
							class="grid items-center gap-2 rounded border px-3 py-2 sm:grid-cols-[1fr_1fr_1fr_auto]"
						>
							<input type="hidden" name="key" value={term.key} />
							<div class="min-w-0">
								<div class="flex items-center gap-2">
									<span class="font-mono text-xs">{term.key}</span>
									{#if term.overridden}
										<Badge variant="secondary" class="font-normal">custom</Badge>
									{/if}
								</div>
								{#if term.description}
									<span class="text-muted-foreground block truncate text-xs">
										{term.description}
									</span>
								{/if}
							</div>
							<Input
								name="label"
								value={term.label ?? ''}
								placeholder={term.default_label}
								aria-label="{term.key} singular"
								disabled={!canManage}
							/>
							<Input
								name="plural"
								value={term.plural ?? ''}
								placeholder={term.default_plural ?? ''}
								aria-label="{term.key} plural"
								disabled={!canManage}
							/>
							<div class="flex items-center gap-1">
								<Button type="submit" size="sm" variant="outline" disabled={!canManage}>Save</Button
								>
								{#if term.overridden}
									<!-- Reset is the same call with a blank label: the API clears the override
									     rather than storing an empty string, so "default" and "called nothing"
									     cannot be confused. -->
									<Button
										type="submit"
										size="sm"
										variant="ghost"
										disabled={!canManage}
										onclick={(event) => {
											const scope = (event.currentTarget as HTMLElement).closest('form');
											const label = scope?.querySelector<HTMLInputElement>('input[name="label"]');
											const plural = scope?.querySelector<HTMLInputElement>('input[name="plural"]');
											if (label) label.value = '';
											if (plural) plural.value = '';
										}}
										title="Reset to default"
									>
										<RotateIcon class="size-4" />
									</Button>
								{/if}
							</div>
						</form>
					{/each}
				</div>
			</section>
		{/each}
	</CardContent>
</Card>
