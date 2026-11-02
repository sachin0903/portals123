<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button, Card, CardContent, Input, Label, PageHeader } from '@graphene/ui';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<svelte:head><title>Illustrations</title></svelte:head>

<div class="space-y-6">
	<PageHeader
		title="Illustration reference data"
		description="Kept for reference. A GIA or ISA illustration does not read these tables. Growth is the model’s target return less 2%, and the platform charge is a fee package on the firm."
	/>
	{#if form && 'error' in form && form.error}
		<p class="text-destructive text-sm">{form.error}</p>
	{/if}

	<Card>
		<CardContent class="space-y-3">
			<h2 class="font-medium">Growth rates</h2>
			{#if data.canWrite}
				<form method="POST" action="?/growth" use:enhance class="grid gap-3 md:grid-cols-4">
					<div class="space-y-1">
						<Label for="g-from">Effective from</Label><Input
							id="g-from"
							name="effective_from"
							type="date"
							required
						/>
					</div>
					<div class="space-y-1">
						<Label for="g-eq">Equity</Label><Input id="g-eq" name="equity_rate" required />
					</div>
					<div class="space-y-1">
						<Label for="g-gi">Gilts</Label><Input id="g-gi" name="gilts_rate" required />
					</div>
					<div class="space-y-1">
						<Label for="g-bo">Bonds</Label><Input id="g-bo" name="bonds_rate" required />
					</div>
					<div class="space-y-1">
						<Label for="g-ot">Other</Label><Input id="g-ot" name="other_rate" required />
					</div>
					<div class="space-y-1">
						<Label for="g-sp">Spread</Label><Input id="g-sp" name="spread" value="0.03" />
					</div>
					<div class="space-y-1">
						<Label for="g-h">GIA haircut</Label><Input id="g-h" name="gia_haircut" value="0.005" />
					</div>
					<div class="space-y-1">
						<Label for="g-i">Inflation</Label><Input id="g-i" name="inflation" value="0.02" />
					</div>
					<label class="flex items-center gap-2 text-sm md:col-span-2"
						><input type="checkbox" name="approved" /> Approved for use</label
					>
					<Button type="submit" class="md:col-span-4 md:w-fit">Save growth set</Button>
				</form>
			{/if}
			<ul class="text-muted-foreground text-sm">
				{#each data.growthSets as row (row.uid)}
					<li>
						{row.effective_from}: equity {row.equity_rate}{row.approved_at
							? ' · approved'
							: ' · draft'}
					</li>
				{:else}
					<li>No growth set yet. Generation stays blocked until one is approved.</li>
				{/each}
			</ul>
		</CardContent>
	</Card>

	<Card>
		<CardContent class="space-y-3">
			<h2 class="font-medium">ISA allowance</h2>
			{#if data.canWrite}
				<form method="POST" action="?/allowance" use:enhance class="grid gap-3 md:grid-cols-3">
					<div class="space-y-1">
						<Label for="isa-y">Tax year</Label><Input
							id="isa-y"
							name="tax_year"
							placeholder="2026/27"
							required
						/>
					</div>
					<div class="space-y-1">
						<Label for="isa-a">Allowance (£)</Label><Input id="isa-a" name="allowance" required />
					</div>
					<div class="space-y-1">
						<Label for="isa-d">Effective from</Label><Input
							id="isa-d"
							name="effective_from"
							type="date"
							required
						/>
					</div>
					<Button type="submit" class="md:col-span-3 md:w-fit">Save allowance</Button>
				</form>
			{/if}
			<ul class="text-muted-foreground text-sm">
				{#each data.allowances as row (row.tax_year)}
					<li>{row.tax_year}: £{row.allowance}</li>
				{:else}
					<li>No allowance recorded.</li>
				{/each}
			</ul>
		</CardContent>
	</Card>

	<Card>
		<CardContent class="space-y-3">
			<h2 class="font-medium">Custodian cash and footer</h2>
			{#if data.canWrite}
				<form method="POST" action="?/custody" use:enhance class="grid gap-3 md:grid-cols-3">
					<div class="space-y-1">
						<Label for="c-p">Custodian</Label>
						<select
							id="c-p"
							name="provider"
							class="border-input bg-background h-9 w-full rounded-md border px-2 text-sm"
						>
							<option value="seccl">Seccl</option>
							<option value="sei">SEI</option>
							<option value="morgan-lloyd">Morgan Lloyd</option>
						</select>
					</div>
					<div class="space-y-1">
						<Label for="c-d">Effective from</Label><Input
							id="c-d"
							name="effective_from"
							type="date"
							required
						/>
					</div>
					<div class="space-y-1">
						<Label for="c-n">Legal name</Label><Input id="c-n" name="custodian_name" required />
					</div>
					<div class="space-y-1">
						<Label for="c-f">FCA number</Label><Input id="c-f" name="custodian_fca" required />
					</div>
					<div class="space-y-1">
						<Label for="c-paid">Cash paid to the client</Label><Input
							id="c-paid"
							name="cash_paid_rate"
							value="0"
						/>
					</div>
					<div class="space-y-1">
						<Label for="c-kept">Cash retained</Label><Input
							id="c-kept"
							name="cash_retention_rate"
							value="0"
						/>
					</div>
					<Button type="submit" class="md:col-span-3 md:w-fit">Save custodian row</Button>
				</form>
			{/if}
			<ul class="text-muted-foreground text-sm">
				{#each data.custody as row (`${row.provider}-${row.effective_from}`)}
					<li>
						{row.provider} from {row.effective_from}: {row.custodian_name} (FCA {row.custodian_fca})
					</li>
				{:else}
					<li>No custodian row yet.</li>
				{/each}
			</ul>
		</CardContent>
	</Card>

	<Card>
		<CardContent class="space-y-3">
			<h2 class="font-medium">Instrument costs</h2>
			{#if data.canWrite}
				<form method="POST" action="?/costs" use:enhance class="space-y-2">
					<Label for="costs"
						>JSON list (isin, data_date, instrument_type, ongoing, transaction_cost)</Label
					>
					<textarea
						id="costs"
						name="rows"
						rows="6"
						class="border-input bg-background w-full rounded-md border p-2 font-mono text-xs"
						placeholder={'[{"isin":"IE00BTEST0001","data_date":"2026-09-01","instrument_type":"fund","ongoing":"0.002","transaction_cost":"0.0004"}]'}
					></textarea>
					<Button type="submit">Import costs</Button>
				</form>
			{/if}
			<ul class="text-muted-foreground text-sm">
				{#each data.costs as row (`${row.isin}-${row.data_date}`)}
					<li>{row.isin} · {row.data_date} · {row.instrument_type} · ongoing {row.ongoing}</li>
				{:else}
					<li>No instrument costs imported.</li>
				{/each}
			</ul>
		</CardContent>
	</Card>
</div>
