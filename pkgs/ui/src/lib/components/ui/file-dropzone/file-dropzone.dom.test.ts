import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import FileDropzone from './file-dropzone.svelte';

vi.useFakeTimers({ toFake: ['Date'] });

class TestDataTransfer {
	private selected: File[] = [];
	items = { add: (file: File) => this.selected.push(file) };
	get files(): FileList {
		return this.selected as unknown as FileList;
	}
}
vi.stubGlobal('DataTransfer', TestDataTransfer);

describe('FileDropzone native form participation', () => {
	it('keeps every accepted file on the named input', () => {
		const host = document.createElement('div');
		document.body.appendChild(host);
		const component = mount(FileDropzone, {
			target: host,
			props: { name: 'files', multiple: true, accept: '.xlsx' }
		});
		const input = host.querySelector('input[type="file"]') as HTMLInputElement;
		const chosen = [new File(['one'], 'one.xlsx'), new File(['two'], 'two.xlsx')];
		Object.defineProperty(input, 'files', {
			value: [] as unknown as FileList,
			writable: true,
			configurable: true
		});
		const transfer = new DataTransfer();
		for (const file of chosen) transfer.items.add(file);
		input.files = transfer.files;
		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(input.name).toBe('files');
		expect([...input.files!].map((file) => file.name)).toEqual(['one.xlsx', 'two.xlsx']);

		unmount(component);
		host.remove();
	}, 20_000);
});
