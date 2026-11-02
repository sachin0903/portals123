import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import FolderIcon from '@lucide/svelte/icons/folder';
import Segmented from './segmented.svelte';

const options = [
	{ value: 'a', label: 'Folders', ariaLabel: 'Show as folders', icon: FolderIcon },
	{ value: 'b', label: 'List' }
];

describe('Segmented', () => {
	it('names icon-only segments by aria-label and title, keeps text where there is no icon, and `lg` is a 44px tap target', () => {
		const body = render(Segmented, {
			props: { options, value: 'a', iconOnly: true, size: 'lg' }
		}).body;
		expect(body).toContain('aria-label="Show as folders"');
		expect(body).toContain('title="Show as folders"');
		expect(body).toMatch(/>\s*List\s*</);
		expect(body).toContain('h-12');
		expect(body).toContain('h-11');
	});
});
