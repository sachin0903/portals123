/** A firm-owned client group, as the studio and the client record both read it. */

export type ClientGroup = {
	uid: string;
	name: string;
	slug: string;
	description?: string;
	sort_order: number;
};

export type GroupDraft = {
	uid: string | null;
	name: string;
	slug: string;
	description: string;
	sort_order: number;
};

export function emptyGroupDraft(sortOrder = 0): GroupDraft {
	return { uid: null, name: '', slug: '', description: '', sort_order: sortOrder };
}

export function groupDraftFrom(row: ClientGroup): GroupDraft {
	return {
		uid: row.uid,
		name: row.name,
		slug: row.slug,
		description: row.description ?? '',
		sort_order: row.sort_order
	};
}
