/**
 * @vitest-environment jsdom
 *
 * The email field names a bad address under the box AS IT IS TYPED, the same as the phone field.
 * Before, it was a bare box with the browser's own check, which only speaks on submit — and on a
 * form posted through `fetch`, not at all.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import EmailInput from './email-input.svelte';

afterEach(() => {
	document.body.innerHTML = '';
});

function type(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function render(props: Record<string, unknown> = {}) {
	const host = document.createElement('div');
	document.body.append(host);
	mount(EmailInput, { target: host, props: { id: 'em', ...props } });
	flushSync();
	return { host, input: host.querySelector('input') as HTMLInputElement };
}

describe('EmailInput', () => {
	it('names a bad address under the box as it is typed, and clears it once it is fixed', () => {
		const onchange = vi.fn();
		const { host, input } = render({ onchange });
		type(input, 'theo@caldwell');
		expect(input.getAttribute('aria-invalid')).toBe('true');
		expect(input.getAttribute('aria-describedby')).toBe('em-issue');
		expect(host.querySelector('#em-issue')?.textContent).toContain('Enter an email address');
		expect(onchange).toHaveBeenLastCalledWith('theo@caldwell');

		type(input, 'theo@caldwell.test');
		expect(input.getAttribute('aria-invalid')).toBeNull();
		expect(host.querySelector('#em-issue')).toBeNull();
		expect(onchange).toHaveBeenLastCalledWith('theo@caldwell.test');
	});

	it('says nothing about an empty field', () => {
		const { host, input } = render();
		expect(host.querySelector('#em-issue')).toBeNull();
		type(input, 'x');
		type(input, '');
		expect(host.querySelector('#em-issue')).toBeNull();
	});

	it('posts by name with a plain form, and passes required and disabled through', () => {
		const { input } = render({ name: 'email', required: true, disabled: true, value: 'a@b.co' });
		expect(input.name).toBe('email');
		expect(input.type).toBe('email');
		expect(input.required).toBe(true);
		expect(input.disabled).toBe(true);
		expect(input.value).toBe('a@b.co');
	});
});
