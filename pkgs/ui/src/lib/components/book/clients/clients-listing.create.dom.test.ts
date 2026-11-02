/**
 * @vitest-environment jsdom
 *
 * The New client drawer's mobile is the shared phone field, so a bad number is named under the
 * box AS IT IS TYPED, not only once Create has sent it to the API and fetched a refusal back. The
 * drawer had a bare `<input type="tel">` that took `1111111` and letters without a word.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ClientsListing from './clients-listing.svelte';
import type { Enhance } from './form-enhance.js';

const enhance = (() => ({ destroy() {} })) as unknown as Enhance;
const links = { client: (uid: string) => `/clients/${uid}` };

afterEach(() => {
	document.body.innerHTML = '';
});

function type(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function openDrawer() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const view = mount(ClientsListing, {
		target,
		props: { clients: [], links, actions: { enhance, create: '?/create' }, initialCreate: true }
	});
	flushSync();
	const field = (id: string) => document.getElementById(id) as HTMLInputElement;
	const create = () =>
		[...document.querySelectorAll('button')].find((b) =>
			b.textContent?.includes('Create client')
		) as HTMLButtonElement;
	return { view, field, create };
}

describe('New client drawer — mobile and email', () => {
	it('names a bad mobile under the box as it is typed, and holds Create until it is fixed', () => {
		const { view, field, create } = openDrawer();
		const mobile = field('new-client-mobile');
		expect(mobile).toBeTruthy();
		type(field('new-client-surname'), 'Caldwell');
		expect(create().disabled).toBe(false);

		type(mobile, '1111111');
		expect(mobile.getAttribute('aria-invalid')).toBe('true');
		expect(document.getElementById('new-client-mobile-issue')?.textContent).toContain(
			'UK number starting 0'
		);
		expect(create().disabled).toBe(true);

		type(mobile, '07700 900123');
		expect(mobile.getAttribute('aria-invalid')).toBeNull();
		expect(document.getElementById('new-client-mobile-issue')).toBeNull();
		expect(create().disabled).toBe(false);
		unmount(view);
	});

	it('refuses a typed letter at the keystroke', () => {
		const { view, field } = openDrawer();
		const event = new InputEvent('beforeinput', {
			bubbles: true,
			cancelable: true,
			data: 'a',
			inputType: 'insertText'
		});
		field('new-client-mobile').dispatchEvent(event);
		expect(event.defaultPrevented).toBe(true);
		unmount(view);
	});

	it('names a bad email under the box as it is typed, and holds Create until it is fixed', () => {
		const { view, field, create } = openDrawer();
		type(field('new-client-surname'), 'Caldwell');
		const email = field('new-client-email');
		type(email, 'theo@caldwell');
		expect(email.getAttribute('aria-invalid')).toBe('true');
		expect(document.getElementById('new-client-email-issue')?.textContent).toContain(
			'Enter an email address'
		);
		expect(create().disabled).toBe(true);

		type(email, 'theo@caldwell.test');
		expect(document.getElementById('new-client-email-issue')).toBeNull();
		expect(create().disabled).toBe(false);
		unmount(view);
	});

	it('an empty mobile is not an error — it is optional', () => {
		const { view, field, create } = openDrawer();
		type(field('new-client-surname'), 'Caldwell');
		expect(field('new-client-mobile').value).toBe('');
		expect(document.getElementById('new-client-mobile-issue')).toBeNull();
		expect(create().disabled).toBe(false);
		unmount(view);
	});
});
