<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button, Card, CardContent, Input, Label, PageHeader } from '@graphene/ui';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const disclosure = $derived(data.disclosure ?? {});
</script>

<svelte:head><title>Illustrations · {data.tenant.name}</title></svelte:head>

<div class="space-y-6">
	<PageHeader
		eyebrow={data.tenant.name}
		title="GIA and ISA illustrations"
		description="Legal disclosure for the PDF, and whether this firm issues GIA and ISA illustrations. The platform charge is a fee package on the firm, and growth is the model’s target return."
	/>
	{#if form && 'error' in form && form.error}
		<p class="text-destructive text-sm">{form.error}</p>
	{/if}

	<Card>
		<CardContent class="space-y-3">
			<h2 class="font-medium">Disclosure</h2>
			{#if data.canWrite}
				<form method="POST" action="?/disclosure" use:enhance class="grid gap-3 md:grid-cols-2">
					<div class="space-y-1">
						<Label for="legal">Legal entity</Label><Input
							id="legal"
							name="legal_name"
							value={disclosure.legal_name ?? ''}
						/>
					</div>
					<div class="space-y-1">
						<Label for="frn">FCA number</Label><Input
							id="frn"
							name="frn"
							value={disclosure.frn ?? ''}
						/>
					</div>
					<div class="space-y-1">
						<Label for="chn">Company number</Label><Input
							id="chn"
							name="chn"
							value={disclosure.chn ?? ''}
						/>
					</div>
					<div class="space-y-1 md:col-span-2">
						<Label for="office">Registered office</Label><Input
							id="office"
							name="registered_office"
							value={disclosure.registered_office ?? ''}
						/>
					</div>
					<div class="space-y-1 md:col-span-2">
						<Label for="warning">Future-performance warning</Label>
						<textarea
							id="warning"
							name="illustration_warning"
							rows="3"
							class="border-input bg-background w-full rounded-md border p-2 text-sm"
							>{disclosure.illustration_warning ?? ''}</textarea
						>
					</div>
					<label class="flex items-center gap-2 text-sm md:col-span-2">
						<input
							type="checkbox"
							name="gia_isa_illustrations"
							checked={disclosure.gia_isa_illustrations}
						/>
						This firm issues GIA and ISA illustrations
					</label>
					<label class="flex items-start gap-2 text-sm md:col-span-2">
						<input
							type="checkbox"
							name="illustrations_auto_share"
							checked={disclosure.illustrations_auto_share}
							class="mt-0.5"
						/>
						<span>
							Show every new illustration in the client portal as soon as it is generated
							<span class="text-muted-foreground block text-xs">
								All illustrations, SIPP included. Off, an illustration stays with the firm until a
								member of staff shares it from the client’s illustrations list.
							</span>
						</span>
					</label>
					<Button type="submit" class="md:w-fit">Save disclosure</Button>
				</form>
			{:else}
				<p class="text-muted-foreground text-sm">
					{disclosure.legal_name ?? 'No legal entity recorded.'}
				</p>
			{/if}
		</CardContent>
	</Card>

	<Card>
		<CardContent class="space-y-2">
			<h2 class="font-medium">Platform fee</h2>
			<p class="text-muted-foreground text-sm">
				Taken from this firm’s fee packages whose category contains “platform”. When there is more
				than one, the adviser chooses it on the illustration. A separate illustration fee schedule
				is not used.
			</p>
		</CardContent>
	</Card>

	<Card>
		<CardContent class="space-y-2">
			<h2 class="font-medium">Growth</h2>
			<p class="text-muted-foreground text-sm">
				Mid growth is the model’s target return less 2 percentage points. Low and high are 3 points
				either side. Asset mix, holdings and per-fund costs are not required.
			</p>
		</CardContent>
	</Card>
</div>
