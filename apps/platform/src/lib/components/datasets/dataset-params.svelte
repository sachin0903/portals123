<script lang="ts">
	/**
	 * The settings form for a provided dataset — what a firm may change about a query Graphene wrote.
	 *
	 * A DATE SETTING HAS TWO MODES, and this is the whole reason it is not a date picker. A date can
	 * be a ROLLING range (`-12m`) or a FIXED one (`2026-04-06`), and the difference matters on every
	 * render: a rolling range keeps meaning "the last twelve months", where a fixed one means the
	 * twelve months that were current when somebody filled this form in. A quarterly statement set
	 * up once with a date picker would report the same fixed quarter for ever, so offering only a
	 * picker would quietly discard the more useful of the two.
	 *
	 * So each date renders as a menu of rolling options with "A fixed date" as one entry, and the
	 * date box appears only when that is chosen. The value posted is the TOKEN — `-12m`, `today`,
	 * `2026-04-06` or empty — and the API stores it verbatim, resolving it per render.
	 *
	 * Every control is a `@graphene/ui` one (`docs/design-language.md`: never a native control), and
	 * the values are posted through hidden inputs so the server action stays a plain form handler.
	 */
	import { Input, Label, SelectMenu, Switch } from '@graphene/ui';
	import type { ApiDatasetParam } from '$lib/datasets';

	let {
		params,
		values = $bindable({})
	}: {
		params: ApiDatasetParam[];
		/** Parameter name → the token to post. Seeded from the shipped defaults on mount. */
		values?: Record<string, string>;
	} = $props();

	/**
	 * The rolling options offered for a date. Deliberately a short list of the periods a document
	 * actually reports on rather than a free "N months back" box: every one of these is a token the
	 * API already accepts, so the menu cannot offer something the server would refuse.
	 */
	const ROLLING = [
		{ value: '', label: 'No limit' },
		{ value: 'today', label: 'Today' },
		{ value: '-1m', label: 'Last month' },
		{ value: '-3m', label: 'Last 3 months' },
		{ value: '-6m', label: 'Last 6 months' },
		{ value: '-12m', label: 'Last 12 months' },
		{ value: '-2y', label: 'Last 2 years' },
		{ value: 'fixed', label: 'A fixed date…' }
	];
	const ROLLING_TOKENS = new Set(ROLLING.map((o) => o.value));

	/** An ISO date is a fixed one; anything else is a rolling token this menu knows. */
	function isFixed(token: string): boolean {
		return token !== '' && !ROLLING_TOKENS.has(token);
	}

	// Which mode each date control is showing. Derived from the stored token on first render so
	// re-opening a configured dataset lands on the mode it was saved in.
	let mode = $state<Record<string, string>>({});

	function tokenFor(p: ApiDatasetParam): string {
		return values[p.name] ?? String(p.default ?? '');
	}

	function modeFor(p: ApiDatasetParam): string {
		return mode[p.name] ?? (isFixed(tokenFor(p)) ? 'fixed' : tokenFor(p));
	}

	function setMode(p: ApiDatasetParam, next: string): void {
		mode = { ...mode, [p.name]: next };
		// Choosing a rolling option IS choosing the value. Choosing "a fixed date" clears it, so the
		// box opens empty rather than showing a token as if it were a date.
		values = { ...values, [p.name]: next === 'fixed' ? '' : next };
	}

	function setValue(p: ApiDatasetParam, next: string): void {
		values = { ...values, [p.name]: next };
	}
</script>

{#if params.length > 0}
	<div class="space-y-4">
		{#each params as p (p.name)}
			<div class="space-y-1.5">
				<Label for={`param-${p.name}`}>{p.label}</Label>

				{#if p.kind === 'date'}
					{@const m = modeFor(p)}
					<SelectMenu
						id={`param-${p.name}`}
						items={ROLLING}
						value={m}
						onValueChange={(v) => setMode(p, v)}
					/>
					{#if m === 'fixed'}
						<Input
							type="date"
							aria-label={`${p.label}, a fixed date`}
							value={isFixed(tokenFor(p)) ? tokenFor(p) : ''}
							oninput={(e) => setValue(p, (e.currentTarget as HTMLInputElement).value)}
						/>
					{/if}
				{:else if p.kind === 'bool'}
					<div class="flex items-center gap-2">
						<Switch
							id={`param-${p.name}`}
							checked={tokenFor(p) === 'true'}
							onCheckedChange={(on) => setValue(p, on ? 'true' : 'false')}
						/>
						<span class="text-muted-foreground text-sm">{p.help || p.label}</span>
					</div>
				{:else if p.kind === 'enum'}
					<SelectMenu
						id={`param-${p.name}`}
						items={p.options.map((o) => ({ value: o.value, label: o.label }))}
						value={tokenFor(p)}
						onValueChange={(v) => setValue(p, v)}
					/>
				{:else}
					<Input
						id={`param-${p.name}`}
						type="number"
						min={p.minimum ?? undefined}
						max={p.maximum ?? undefined}
						value={tokenFor(p)}
						oninput={(e) => setValue(p, (e.currentTarget as HTMLInputElement).value)}
					/>
				{/if}

				{#if p.help && p.kind !== 'bool'}
					<p class="text-muted-foreground text-xs">{p.help}</p>
				{/if}

				<!-- The token, not the rendered date: the API resolves it on every render. -->
				<input type="hidden" name={`param_${p.name}`} value={tokenFor(p)} />
			</div>
		{/each}
	</div>
{/if}
