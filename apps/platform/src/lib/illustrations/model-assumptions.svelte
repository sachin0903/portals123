<script lang="ts">
	// What the chosen model contributes to the projection, stated in a line.
	//
	// It exists because "is the model's charge being applied?" was a real question with no visible
	// answer: the growth rate and the charge are both properties of the model rather than of this
	// form, so an adviser filling the form in had no way to tell whether either had been picked up.
	// (The charge in fact had NOT been — `_premium_split` never selected `model_charge`, so every
	// projection ran gross of it. Fixed in the API; this is what makes the fix legible.)
	//
	// Its own component, beside the one `illustrationFields`, for the same reason: a statement about
	// the assumptions must not be able to disagree with the form that collects them.
	import type { IllustrationModel } from '$lib/illustrations/form';

	let {
		models = [],
		/** The form's current `model_id`. A string, as the picker stores it. */
		selected
	}: {
		models?: IllustrationModel[];
		selected?: unknown;
	} = $props();

	const model = $derived(models.find((m) => String(m.id) === String(selected ?? '')) ?? null);
	/** Both come off the record as numeric strings via JSON, so they are normalised here rather than
	 *  interpolated raw — "6.5000%" is the same number rendered as though it were measured. */
	const rate = $derived(asRate(model?.target_return));
	const charge = $derived(asRate(model?.model_charge));

	function asRate(value: number | string | null | undefined): string | null {
		if (value === null || value === undefined || value === '') return null;
		const n = Number(value);
		if (!Number.isFinite(n)) return null;
		return `${Number(n.toFixed(4))}%`;
	}
</script>

{#if model && rate}
	<!-- "Targets", not "applied" — and not "is sent at", which read as a postage rate. CTC overrides
	     the fund-level growth rate on an accumulation quote with the COBS standard rates
	     (docs/morgan-lloyd/illustrations-report.md §4), so what we put in the request is not proof of
	     what the document projects at; the closing sentence says so. -->
	<p class="text-muted-foreground text-xs">
		<strong class="text-foreground font-medium">{model.name}</strong>
		targets a return of {rate} a year,
		{#if charge}
			with a {charge} annual model charge.
		{:else}
			<!-- Said, not skipped. A model with no charge recorded and a model that genuinely charges
			     nothing look identical on the document, and only one of them is a data gap. -->
			with no model charge recorded.
		{/if}
		Adviser fees are applied separately. An accumulation illustration may use standard growth rates instead
		of this model rate.
	</p>
{/if}
