/**
 * The headline count-up's maths — pure, so the number the animation LANDS on is provable
 * without mounting anything. The animation itself (a Tween over `t` in [0, 1]) lives in
 * animated-money.svelte; everything numeric lives here.
 *
 * The figure rises from 90% of the value rather than from zero: a from-zero count reads as a
 * slot machine, while a short rise into place reads as the number settling — and critically the
 * order of magnitude is correct for every frame, so a glance mid-animation is never off by 10x.
 */

export const COUNT_UP_MS = 280;
export const COUNT_UP_FROM = 0.9;

/** The displayed value at animation progress `t` (clamped into [0, 1]); t=1 is EXACTLY the
 * target — the animation must land on the true figure, not a float neighbour of it. */
export function countUpAt(target: number, t: number): number {
	const k = Math.min(1, Math.max(0, t));
	if (k === 1) return target;
	return target * (COUNT_UP_FROM + (1 - COUNT_UP_FROM) * k);
}
