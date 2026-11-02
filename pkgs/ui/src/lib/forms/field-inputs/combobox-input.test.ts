/**
 * FB-20261006-06: the illustration form's model picker cut long model names off mid-word, and
 * the list is the only place a name can be read before it is chosen. The option label wraps;
 * only the one-line trigger truncates, and it carries the full name as its title.
 *
 * A source assertion rather than a mounted one: the popover's content is portalled and rendered
 * only once open, and the defect is a class on a span — there is no behaviour for jsdom to observe.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./combobox-input.svelte', import.meta.url), 'utf8');

describe('ComboboxInput', () => {
	it('wraps option labels instead of truncating them', () => {
		const list = source.slice(source.indexOf('{#each filtered as opt'));
		expect(list).toContain(
			'<span class="min-w-0 break-words whitespace-normal">{opt.label}</span>'
		);
		expect(list).not.toContain('<span class="truncate">{opt.label}</span>');
	});

	it('keeps the trigger to one line, with the full label as its title', () => {
		const trigger = source.slice(0, source.indexOf('<Popover.Content'));
		expect(trigger).toContain("'min-w-0 truncate'");
		expect(trigger).toContain('title={selectedLabel || undefined}');
	});
});
