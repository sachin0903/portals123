import { globSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * Card already supplies vertical rhythm (`py-4` / `gap-4`; `sm` is `py-3` / `gap-3`). Extra
 * padding on the header or content STACKS with that, which is how the client record's rail
 * titles sat further from their body than Compliance, and how the metrics tiles grew twice
 * the inset of every neighbouring card.
 *
 * Title → body is the Card's gap. CardHeader therefore carries no `pb-*` of its own unless
 * it is cancelling (`pb-0`) or owning a divided header (`border-b`, whose `[.border-b]:pb-*`
 * is the primitive's own rule). CardContent is horizontal padding only — no `py-*` / `pt-*`
 * / `p-4` leftover from the pre-shadcn `p-6 pt-0` content slot. Empty-state heroes (`py-10`,
 * `py-14`) are the exception: they are the body, not a second frame of padding.
 */

const WORKSPACE = fileURLToPath(new URL('../../../../../../../', import.meta.url));
const ROOTS = ['pkgs/ui/src', 'apps/client/src', 'apps/platform/src', 'apps/admin/src'];

const HEADER_TAG = /<(?:CardHeader|Card\.Header)[^>]*>/g;
const CONTENT_TAG = /<(?:CardContent|Card\.Content)[^>]*>/g;

function tags(pattern: RegExp): { file: string; tag: string }[] {
	const found: { file: string; tag: string }[] = [];
	for (const root of ROOTS) {
		for (const file of globSync(`${WORKSPACE}${root}/**/*.svelte`)) {
			const source = readFileSync(file, 'utf8');
			for (const [tag] of source.matchAll(pattern)) {
				found.push({ file: file.replaceAll('\\', '/'), tag });
			}
		}
	}
	return found;
}

function classList(tag: string): string[] {
	return (tag.match(/class="([^"]*)"/)?.[1] ?? '').split(/\s+/).filter(Boolean);
}

/** Decorative bottom padding on a header — Card's gap already separates title from body. */
function extraHeaderPad(tag: string): boolean {
	if (/\bborder-b\b/.test(tag)) return false;
	if (/\bpb-0\b/.test(tag)) return false;
	return /\bpb-(?:[1-9]|[1-9]\.\d+)\b/.test(tag);
}

/**
 * Vertical padding on content that stacks with Card's `py-4`. `p-0` / `py-0` opt out of the
 * slot (divided lists). `py-6` and up are empty/hero bodies, not a second frame.
 */
const STACKED_CONTENT = /^(?:py-[1-5]|pt-[1-9]|pb-[1-5]|p-[4-6])$/;

function extraContentPad(tag: string): boolean {
	return classList(tag).some((c) => STACKED_CONTENT.test(c));
}

describe('Card padding is owned by the primitive', () => {
	it('finds the headers and contents it is meant to be scanning', () => {
		expect(tags(HEADER_TAG).length).toBeGreaterThan(60);
		expect(tags(CONTENT_TAG).length).toBeGreaterThan(80);
	});

	it('no CardHeader adds bottom padding on top of Card gap', () => {
		const offenders = tags(HEADER_TAG)
			.filter(({ tag }) => extraHeaderPad(tag))
			.map(({ file, tag }) => `${file.slice(file.indexOf('/src/') + 1)} → ${tag}`);
		expect(offenders).toEqual([]);
	});

	it('no CardContent adds vertical padding on top of Card py', () => {
		const offenders = tags(CONTENT_TAG)
			.filter(({ tag }) => extraContentPad(tag))
			.map(({ file, tag }) => `${file.slice(file.indexOf('/src/') + 1)} → ${tag}`);
		expect(offenders).toEqual([]);
	});

	it('would catch the regressions it exists for', () => {
		expect(extraHeaderPad('<CardHeader class="pb-2">')).toBe(true);
		expect(extraHeaderPad('<CardHeader class="gap-3 pb-2">')).toBe(true);
		expect(extraHeaderPad('<CardHeader class="flex flex-row gap-3 pb-0">')).toBe(false);
		expect(extraHeaderPad('<CardHeader class="border-b px-5 py-3.5 [.border-b]:pb-3.5">')).toBe(
			false
		);
		expect(extraHeaderPad('<CardHeader>')).toBe(false);

		expect(extraContentPad('<CardContent class="py-3">')).toBe(true);
		expect(extraContentPad('<CardContent class="pt-6">')).toBe(true);
		expect(extraContentPad('<Card.Content class="pt-5">')).toBe(true);
		expect(extraContentPad('<CardContent class="space-y-3 p-5 text-sm">')).toBe(true);
		expect(extraContentPad('<CardContent class="flex flex-col gap-3 py-10 text-center">')).toBe(
			false
		);
		expect(extraContentPad('<CardContent class="p-0">')).toBe(false);
		expect(extraContentPad('<CardContent class="flex items-center gap-4">')).toBe(false);
	});
});
