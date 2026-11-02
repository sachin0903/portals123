import { describe, expect, it } from 'vitest';
import { contentForView } from './content-view';

const items = [
	{
		uid: 'draft',
		status: 'draft',
		kind: 'article' as const,
		category_uid: null,
		category_name: null
	},
	{
		uid: 'published',
		status: 'published',
		kind: 'guide' as const,
		category_uid: 'retirement',
		category_name: 'Retirement'
	},
	{
		uid: 'archived',
		status: 'archived',
		kind: 'news' as const,
		category_uid: 'updates',
		category_name: 'Updates'
	}
];

describe('content library views', () => {
	it('keeps archived items out of the current library', () => {
		expect(contentForView(items, 'current').map((item) => item.uid)).toEqual([
			'draft',
			'published'
		]);
	});

	it('shows only archived items in the archive', () => {
		expect(contentForView(items, 'archived').map((item) => item.uid)).toEqual(['archived']);
	});
});
