export type ContentKind = 'news' | 'article' | 'guide';
export type ContentStatus = 'draft' | 'published' | 'archived';
export type ContentAudience = 'all' | 'individual' | 'organisation';

export type ContentCategory = {
	uid: string;
	name: string;
	slug: string;
	sort_order: number;
	created_at: string;
};

export type ContentItem = {
	uid: string;
	kind: ContentKind;
	title: string;
	slug: string;
	summary: string | null;
	status: ContentStatus;
	audience: ContentAudience;
	service_types: string[];
	sub_types: string[];
	published_at: string | null;
	expires_at: string | null;
	hero_url: string | null;
	has_file: boolean;
	category_uid: string | null;
	category_name: string | null;
	created_at: string;
	updated_at: string;
	created_by: string | null;
	body_html?: string | null;
	schema?: { html?: string; css?: string };
	file_content_type?: string | null;
	file_size?: number | null;
};
