import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import IdentityEvidenceUpload from './identity-evidence-upload.svelte';

class TestDataTransfer {
	private selected: File[] = [];
	items = { add: (file: File) => this.selected.push(file) };
	get files(): FileList {
		return this.selected as unknown as FileList;
	}
}
vi.stubGlobal('DataTransfer', TestDataTransfer);

afterEach(() => {
	document.body.innerHTML = '';
});

describe('manual identity evidence upload', () => {
	it('mounts with an empty file list when the manual step first appears', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(IdentityEvidenceUpload, {
			target,
			props: {
				item: {
					key: 'photo_id',
					title: 'Photo ID',
					guidance: 'A current passport',
					format_hint: 'JPG, PNG or PDF',
					request_uid: 'request1',
					item_uid: 'item1',
					status: 'pending'
				},
				action: '?/identityDocument',
				caseUid: 'case1',
				stepKey: 'idv',
				participantContactId: 42,
				enhance: () => {}
			}
		});
		flushSync();

		expect(target.querySelector('input[type="file"]')).not.toBeNull();
		expect(target.querySelector('button[type="submit"]')).toBeNull();
		expect(target.querySelector('[data-slot="file-dropzone"]')).not.toBeNull();
		expect(
			target.querySelector<HTMLInputElement>('input[name="participant_contact_id"]')?.value
		).toBe('42');
		expect(target.querySelector<HTMLInputElement>('input[name="item_uid"]')?.value).toBe('item1');

		unmount(component);
	});

	it('uploads and shows the file as soon as one is chosen', () => {
		const requestSubmit = vi
			.spyOn(HTMLFormElement.prototype, 'requestSubmit')
			.mockImplementation(() => {});
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(IdentityEvidenceUpload, {
			target,
			props: {
				item: {
					key: 'photo_id',
					title: 'Photo ID',
					guidance: 'A current passport',
					format_hint: 'JPG, PNG or PDF',
					request_uid: 'request1',
					item_uid: 'item1',
					status: 'pending'
				},
				action: '?/identityDocument',
				caseUid: 'case1',
				stepKey: 'idv',
				participantContactId: 42,
				enhance: () => {}
			}
		});
		flushSync();

		const input = target.querySelector('input[type="file"]') as HTMLInputElement;
		const chosen = new File(['page'], 'passport.jpg', { type: 'image/jpeg' });
		Object.defineProperty(input, 'files', {
			value: [] as unknown as FileList,
			writable: true,
			configurable: true
		});
		const transfer = new DataTransfer();
		transfer.items.add(chosen);
		input.files = transfer.files;
		input.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		expect(requestSubmit).toHaveBeenCalledOnce();
		expect(target.textContent).toContain('passport.jpg');
		expect(target.querySelector('[data-slot="file-dropzone"]')).toBeNull();
		expect(target.querySelector('[data-slot="file-dropzone-file"]')).not.toBeNull();

		requestSubmit.mockRestore();
		unmount(component);
	});
});
