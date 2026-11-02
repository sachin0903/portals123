// A focused money field keeps what is being typed, and still shows a number that arrives from
// outside — the range slider writing over a figure the box still has focus on.
import { afterEach, describe, expect, it } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import Harness from './money-input-harness.test.svelte';

const live: ReturnType<typeof mount>[] = [];

afterEach(() => {
	for (const component of live.splice(0)) unmount(component);
	document.body.innerHTML = '';
});

async function settle() {
	await tick();
	await new Promise((resolve) => setTimeout(resolve, 20));
	await tick();
}

function mounted() {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(Harness, { target, props: {} });
	live.push(component);
	return component as unknown as {
		external: (next: number | null) => void;
		read: () => number | null;
	};
}

const box = () => document.querySelector('input') as HTMLInputElement;

describe('MoneyInput, mounted', () => {
	it('does not rewrite a decimal the user is in the middle of typing', async () => {
		const field = mounted();
		const input = box();
		input.focus();
		input.value = '1.';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(input.value).toBe('1.');
		expect(field.read()).toBe(1);
		expect(document.activeElement).toBe(input);
	});

	it('shows an amount pushed in from outside while the field is focused', async () => {
		const field = mounted();
		const input = box();
		input.focus();
		input.value = '1000';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(field.read()).toBe(1000);
		field.external(9_600_000);
		await settle();
		expect(input.value).toBe('9,600,000.00');
		expect(document.activeElement).toBe(input);
	});

	// The bordered box is the field. A tap on the £ or on the padding beside the digits used to
	// land on a div and do nothing.
	it('focuses the input from anywhere in its box', async () => {
		mounted();
		await settle(); // `bind:this` lands after the first tick
		const input = box();
		const wrapper = document.querySelector('[data-slot="money-input"]') as HTMLElement;
		const symbol = wrapper.querySelector('span') as HTMLElement;
		symbol.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
		await settle();
		expect(document.activeElement).toBe(input);
	});
});
