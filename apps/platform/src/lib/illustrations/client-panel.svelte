<script lang="ts" module>
	/** Age at a date of birth, today. Exported for tests. */
	export function ageOn(dob: string | null | undefined, now = new Date()): number | null {
		if (!dob) return null;
		const born = new Date(dob);
		if (Number.isNaN(born.getTime())) return null;
		const before =
			now.getMonth() < born.getMonth() ||
			(now.getMonth() === born.getMonth() && now.getDate() < born.getDate());
		return now.getFullYear() - born.getFullYear() - (before ? 1 : 0);
	}
</script>

<script lang="ts">
	// The client facts an illustration is built from, as TEXT.
	//
	// Deliberately not inputs. A form that lets someone retype a date of birth — or a marital
	// status the record already holds — is a form that lets the illustration disagree with the
	// record it is filed against. Retirement age is the exception, and it is asked on the form
	// below with nothing filled in: printing the stored age here is how it looked already answered.
	// A marital status the record does NOT hold yet is asked by the form itself
	// (`illustrationFieldsFor`) and written back when the illustration is generated, so this panel
	// never grows a control.
	import { Card, CardContent, clientTypeLabel, formatDate, Money } from '@graphene/ui';
	import { maritalLabel, type IllustrationClient } from '$lib/illustrations/client';

	let {
		clientName,
		client,
		/** Sum of the client's other SIPP accounts. Null when they have none — the line is
		    hidden, which is the whole of a new client's kickoff. The adviser does not edit it. */
		existingSippTotal = null,
		/** Set when the recorded retirement age is already past and the chosen basis changes
		    what the quote can do. The host computes it; this panel stays free of that rule. */
		termNote = null
	}: {
		clientName: string;
		client: IllustrationClient;
		existingSippTotal?: number | null;
		termNote?: string | null;
	} = $props();

	const age = $derived(ageOn(client.dob));
	const marital = $derived(maritalLabel(client.marital_status));

	const facts = $derived([
		{ label: 'Client', value: clientName },
		// "Company / Trust" was printed for EVERY organisation, a charity included, and it named two
		// kinds at once for a client that is only one of them. `clientTypeLabel` is the same function
		// the listing column and the record badge read, so all three say one word.
		{
			label: 'Type',
			value: clientTypeLabel(client.sub_type ?? client.type) ?? 'Individual'
		},
		{
			label: 'Date of birth',
			value: formatDate(client.dob, 'Not recorded')
		},
		{ label: 'Age', value: age === null ? '—' : String(age) },
		// Only once the record holds it. A gap is a field on the form below, not a second "Not
		// recorded" sitting above the input that asks for it. Retirement age is always that field,
		// so it is not printed here.
		...(marital ? [{ label: 'Marital status', value: marital }] : [])
	]);
</script>

<Card glass>
	<CardContent>
		<dl class="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
			{#each facts as fact (fact.label)}
				<div class="min-w-0">
					<dt class="text-muted-foreground text-xs">{fact.label}</dt>
					<dd class="truncate text-sm font-medium" title={fact.value}>{fact.value}</dd>
				</div>
			{/each}
		</dl>
		{#if termNote}
			<p class="text-muted-foreground mt-4 text-sm">{termNote}</p>
		{/if}
		{#if existingSippTotal != null}
			<div class="border-border mt-4 border-t pt-4">
				<p class="text-muted-foreground text-xs">Existing SIPPs</p>
				<p class="text-2xl font-semibold tracking-tight tabular-nums">
					<Money amount={existingSippTotal} />
				</p>
			</div>
		{/if}
	</CardContent>
</Card>
