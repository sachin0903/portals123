/**
 * Account grouping — the `group_by` block setting, made real. One pure implementation shared by
 * the cards variant (real sections with subtotals), the stack variant (grouped-adjacent sort) and
 * the summary variant's owner axis, so "which bucket does this wrapper land in" is answered once
 * and unit-tested here rather than re-derived per component.
 *
 * The family case is the reason this exists: a household's slice interleaves owners (Daniel's
 * ISA/SIPP/GIA, Laura's ISA, two children's JISAs) and the flat deck reads as one person's
 * accounts. Grouping by owner puts each family member's money under their own name.
 */
import type { Wrapper } from './types.js';

/** The grouping axes the accounts block supports (block-contract.json `group_by`). */
export type GroupAxis = 'none' | 'wrapper' | 'owner';

const AXES: readonly GroupAxis[] = ['none', 'wrapper', 'owner'];

/**
 * A stored `group_by` setting, defensively normalised. Settings ride the wire as untyped JSON, so
 * junk — a stale value (the setting shipped dead with a 'product_type' option nothing read), a
 * hand-edited theme, a number — degrades to 'none' (today's flat look), never a crash. Mirrors
 * app/branding.py::AccountsSettings' clamp.
 */
export function normaliseGroupBy(raw: unknown): GroupAxis {
	return typeof raw === 'string' && (AXES as readonly string[]).includes(raw)
		? (raw as GroupAxis)
		: 'none';
}

/** One rendered section of a grouped accounts block. */
export interface WrapperSection {
	/** The owner's name / the product type — or the blank-value fallback bucket. */
	label: string;
	/** Σ value of the section's wrappers, for the section header's right-aligned subtotal. */
	subtotal: number;
	wrappers: Wrapper[];
}

/**
 * The bucket a wrapper files under on an axis. Blank owners land in 'Other accounts' — a real
 * book can carry rows with no holder name, and dropping them would silently under-state the
 * client's money. Blank product types file under 'Account', matching deriveWrapperGroups
 * (slices.ts) so the summary tiles and the grouped cards never name the same bucket differently.
 */
export function groupLabel(w: Wrapper, axis: 'wrapper' | 'owner'): string {
	if (axis === 'owner') return (w.owner ?? '').trim() || 'Other accounts';
	return (w.type ?? '').trim() || 'Account';
}

/**
 * Group a wrappers slice for display. Sections appear in FIRST-APPEARANCE order (the server's
 * ordering usually puts the client's own accounts first — value-sorting would demote a parent
 * below a child's larger JISA and reshuffle on every valuation), and rows keep their original
 * order within a section, so the grouped deck is a stable permutation of the flat one. Empty
 * groups cannot exist by construction — a section only exists because a wrapper filed into it.
 */
export function groupWrappers(wrappers: Wrapper[], axis: 'wrapper' | 'owner'): WrapperSection[] {
	const sections = new Map<string, WrapperSection>();
	for (const w of wrappers) {
		const label = groupLabel(w, axis);
		const section = sections.get(label);
		if (section) {
			section.subtotal += w.value;
			section.wrappers.push(w);
		} else {
			sections.set(label, { label, subtotal: w.value, wrappers: [w] });
		}
	}
	return [...sections.values()];
}

/**
 * Whether the cards variant should present a wrapper as a named beneficiary's account: a JISA is
 * legally the CHILD's money (the adult only manages it), so the card reads "For Freya" rather
 * than listing the child as just another account holder. Deliberately narrow — only the exact
 * 'JISA' product code with a non-blank owner qualifies; free-text types stay on the plain owner
 * line rather than guessing at beneficiary semantics.
 */
export function isBeneficiary(w: Wrapper): boolean {
	return w.type === 'JISA' && (w.owner ?? '').trim() !== '';
}
