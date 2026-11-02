export type TermItem = {
	uid: string;
	owner: string;
	code: string;
	title: string;
	audience: string;
	required_at: string[];
	applicability: Record<string, unknown>;
	status: string;
	body_kind: string;
	published_at: string | null;
	current_version_uid: string | null;
	current_version_number: number | null;
	has_file: boolean;
	created_at: string;
	updated_at: string;
	body_md?: string | null;
	body_html?: string | null;
	file_content_type?: string | null;
	file_size?: number | null;
};

export type TermVersion = {
	uid: string;
	version_number: number;
	published_at: string;
};
