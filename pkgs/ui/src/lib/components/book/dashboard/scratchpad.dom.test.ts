/**
 * The rail's notes. Stored centrally, so what the tests pin is the contract with the API: every
 * edit shows at once and is sent as the right call, a failed save is put back and said, and the
 * server's row replaces the optimistic one. The network is a stand-in `NotesClient`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { tick } from 'svelte';
import Scratchpad from './scratchpad.svelte';
import type { NotesClient, StaffNote } from './notes';

afterEach(() => {
	document.body.innerHTML = '';
});

const note = (uid: string, body: string, done = false): StaffNote => ({
	uid,
	body,
	done,
	position: 1
});

type Pad = { client: NotesClient; unmount: () => void };

function open(notes: StaffNote[], over: Partial<NotesClient> = {}): Pad {
	const client: NotesClient = {
		add: vi.fn(async (body: string) => note('srv_1', body)),
		edit: vi.fn(async (uid: string, patch: { body?: string; done?: boolean }) => ({
			...(notes.find((n) => n.uid === uid) ?? note(uid, '')),
			...patch
		})),
		remove: vi.fn(async () => undefined),
		...over
	};
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = mount(Scratchpad, { target, props: { notes, client } });
	flushSync();
	return { client, unmount: () => unmount(component) };
}

const input = () => document.querySelector('input[aria-label="Add a note"]') as HTMLInputElement;
const items = () => [...document.querySelectorAll('li')].map((li) => li.textContent?.trim());
/** Let the stand-in's promise settle (and a rejection reach its catch) before reading the DOM. */
const settle = async () => {
	await new Promise((r) => setTimeout(r, 0));
	await tick();
	flushSync();
};

function type(text: string) {
	input().value = text;
	input().dispatchEvent(new Event('input', { bubbles: true }));
	input().form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
	flushSync();
}

describe('the dashboard notes', () => {
	it('shows the list it was given, in order', () => {
		open([note('a', 'Chase the SIPP transfer'), note('b', 'Call Joan', true)]);
		expect(items()).toEqual(['Chase the SIPP transfer', 'Call Joan']);
		expect(document.querySelector('[aria-pressed="true"]')).not.toBeNull();
	});

	it('adds a note at once, posts it, and takes the server’s row', async () => {
		const pad = open([]);
		type('Chase the SIPP transfer');
		expect(items()).toEqual(['Chase the SIPP transfer']);
		expect(input().value).toBe('');
		await settle();
		expect(pad.client.add).toHaveBeenCalledWith('Chase the SIPP transfer');
		expect(items()).toEqual(['Chase the SIPP transfer']);
		expect(document.querySelectorAll('li').length).toBe(1); // the placeholder was replaced
	});

	it('ignores a blank note', () => {
		const pad = open([]);
		type('   ');
		expect(items()).toEqual([]);
		expect(pad.client.add).not.toHaveBeenCalled();
	});

	it('puts a note back and says so when the save fails', async () => {
		open([], { add: vi.fn(async () => Promise.reject(new Error('down'))) });
		type('Call Joan');
		expect(items()).toEqual(['Call Joan']);
		await settle();
		expect(items()).toEqual([]);
		expect(document.querySelector('[role="status"]')?.textContent).toContain('Couldn’t save');
	});

	it('ticks a note through PATCH and strikes it through', async () => {
		const pad = open([note('a', 'Call Joan')]);
		(document.querySelector('[aria-label="Mark done"]') as HTMLButtonElement).click();
		flushSync();
		expect(document.querySelector('[aria-pressed="true"]')).not.toBeNull();
		expect(document.querySelector('.line-through')).not.toBeNull();
		await settle();
		expect(pad.client.edit).toHaveBeenCalledWith('a', { done: true });
	});

	it('removes a note through DELETE', async () => {
		const pad = open([note('a', 'Call Joan'), note('b', 'Chase the SIPP transfer')]);
		(document.querySelector('[aria-label="Remove note"]') as HTMLButtonElement).click();
		flushSync();
		expect(items()).toEqual(['Chase the SIPP transfer']);
		await settle();
		expect(pad.client.remove).toHaveBeenCalledWith('a');
	});

	it('restores a note the server would not remove', async () => {
		open([note('a', 'Call Joan')], {
			remove: vi.fn(async () => Promise.reject(new Error('down')))
		});
		(document.querySelector('[aria-label="Remove note"]') as HTMLButtonElement).click();
		flushSync();
		expect(items()).toEqual([]);
		await settle();
		expect(items()).toEqual(['Call Joan']);
	});

	it('no longer claims to be on this device', () => {
		open([]);
		expect(document.body.textContent).not.toContain('On this device');
		expect(document.body.textContent).toContain('My notes');
	});
});
