export type ContentView = 'current' | 'archived';

export function contentForView<T extends { status: string }>(
	items: readonly T[],
	view: ContentView
): T[] {
	return items.filter((item) =>
		view === 'archived' ? item.status === 'archived' : item.status !== 'archived'
	);
}
