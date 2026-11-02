<script lang="ts">
	// Three rows over the rail's five border composites, directly in their sector with no disclosure of
	// their own — so each row names itself (a bare `Width` beside a background colour reads as the
	// element's). `borders.ts` holds the rules; writes go via `read.ts::write`, never `addStyle()`.
	import BoxSides from '../../ui/box-sides/box-sides.svelte';
	import ColorInput from '../../ui/color-input/color-input.svelte';
	import PropertyRow from '../../ui/property-row/property-row.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { SIDE_PARTS } from '../../ui/box-sides/box-sides.js';
	import { propertySet, write, type PropertyView } from './read.js';
	import {
		borderPlan,
		borderSub,
		held,
		readBorderState,
		type BorderAction,
		type BorderFamily,
		type BorderRole,
		type BorderState
	} from './borders.js';

	let {
		family,
		brandColours = [],
		disabled = false,
		onchange
	}: {
		family: BorderFamily;
		brandColours?: readonly string[];
		disabled?: boolean;
		onchange: () => void;
	} = $props();

	const members = $derived(
		[family.all, ...Object.values(family.sides)].filter(Boolean) as PropertyView[]
	);
	// `current`, NOT `state`: a local `state` makes every later `$state(…)` parse as a store read on it.
	const current = $derived(readBorderState(family));

	// Options and units come off the MODELS; the per-EDGE sub wins because `border`'s width has its own.
	function firstSub(role: BorderRole): PropertyView | undefined {
		for (const side of Object.values(family.sides)) {
			const sub = borderSub(side, role);
			if (sub) return sub;
		}
		return borderSub(family.all, role);
	}
	const widthSub = $derived(firstSub('width'));
	const styleSub = $derived(firstSub('style'));
	const widthUnits = $derived(widthSub?.control.kind === 'length' ? widthSub.control.units : []);
	const widthMin = $derived(widthSub?.control.kind === 'length' ? widthSub.control.min : undefined);
	const styleOptions = $derived(
		styleSub?.control.kind === 'select' || styleSub?.control.kind === 'choice'
			? styleSub.control.options
			: []
	);

	/** SYNCHRONOUS, AND IT MUST STAY THAT WAY (the `read.ts::writeParts` rule): backbone-undo fuses one
	 *  callstack under one `magicFusionIndex`. A clear on a member carrying nothing is skipped. */
	function apply(actions: BorderAction[], partial = false): void {
		const opts = partial ? { partial: true } : undefined;
		for (const action of actions) {
			if ('clear' in action) {
				if (propertySet(action.target)) action.target.property.clear(opts);
				continue;
			}
			for (const [role, value] of Object.entries(action.values) as [BorderRole, string][]) {
				const sub = borderSub(action.target, role);
				// Via `held`: an unset part answers its DEFAULT, so a raw compare rewrites every live edge.
				if (!sub || held(sub) === value) continue;
				write(sub.property, value, opts);
			}
		}
		onchange();
	}

	/** `partial` is a colour still being dragged: applied to the canvas, kept off the undo stack
	 *  (`read.ts::write`), with one committed write when the gesture ends. */
	function edit(next: BorderState, via: BorderRole, partial = false): void {
		apply(borderPlan(family, next, via), partial);
	}
</script>

{#if members.length}
	<div class="flex flex-col" data-property="borders">
		<!-- Width IS the border's existence, so one clear is enough: no width draws nothing, and the plan
		     removes every declaration. No wrapping row — `BoxSides` draws its own, because its linked and
		     split modes want different ones. -->
		<BoxSides
			idPrefix="sp-border-width"
			label="Border width"
			{disabled}
			set={Object.values(current.widths).some((w) => w !== '')}
			values={current.widths}
			parts={SIDE_PARTS}
			units={widthUnits}
			min={widthMin}
			onchange={(widths) => edit({ ...current, widths }, 'width')}
		/>

		<!-- One style and one colour for the whole box — the trade `borders.ts` sets out; a left stripe stays
		     expressible. NO "Default" ON STYLE, unlike every other dropdown in the rail: clearing a border
		     style returns `solid` (`borders.ts`), so the option was indistinguishable from Solid — and
		     `None` is already the answer to "no border". Width is what deletes a border. `ColorInput`
		     carries its own clear. -->
		<PropertyRow label="Border style" for="sp-border-style" set={current.style !== ''}>
			<SelectMenu
				id="sp-border-style"
				{disabled}
				size="sm"
				value={current.style}
				items={styleOptions.map((o) => ({ value: o.value, label: o.label }))}
				onValueChange={(style) => edit({ ...current, style }, 'style')}
			/>
		</PropertyRow>

		<PropertyRow label="Border colour" for="sp-border-colour" set={current.colour !== ''}>
			<ColorInput
				id="sp-border-colour"
				{disabled}
				value={current.colour}
				swatches={brandColours}
				oninput={(colour) => edit({ ...current, colour }, 'colour', true)}
				onchange={(colour) => edit({ ...current, colour }, 'colour')}
			/>
		</PropertyRow>
	</div>
{/if}
