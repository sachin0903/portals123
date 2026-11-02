/** Split a pasted blob into allowlist entries. Commas, semicolons and any whitespace are separators. */

const SEPARATORS = /[\s,;]+/;

export function canonicalAllowlistEntry(raw: string): string | null {
	const text = raw.trim().toLowerCase();
	if (!text || SEPARATORS.test(text) || text.length > 254) return null;
	if (text.startsWith('@')) {
		const host = text.slice(1);
		return hostOk(host) ? `@${host}` : null;
	}
	const at = text.indexOf('@');
	if (at !== -1) {
		const local = text.slice(0, at);
		const domain = text.slice(at + 1);
		if (!local || text.includes('@', at + 1) || !hostOk(domain)) return null;
		return text;
	}
	return hostOk(text) ? `@${text}` : null;
}

function hostOk(host: string): boolean {
	if (!host || host.startsWith('.') || host.endsWith('.') || host.includes('..')) return false;
	if (host.startsWith('-') || host.endsWith('-')) return false;
	return /^[a-z0-9.-]+$/.test(host);
}

export type AllowlistPaste = {
	entries: string[];
	added: number;
	duplicate: number;
	rejected: number;
};

/** Merge a paste into the current list. Order is kept; duplicates and unreadable tokens are counted. */
export function mergeAllowlistPaste(current: readonly string[], raw: string): AllowlistPaste {
	const seen = new Set(current);
	const entries = [...current];
	let added = 0;
	let duplicate = 0;
	let rejected = 0;
	for (const part of raw.split(SEPARATORS)) {
		if (!part.trim()) continue;
		const entry = canonicalAllowlistEntry(part);
		if (!entry) {
			rejected++;
			continue;
		}
		if (seen.has(entry)) {
			duplicate++;
			continue;
		}
		seen.add(entry);
		entries.push(entry);
		added++;
	}
	return { entries, added, duplicate, rejected };
}

export function allowlistPasteSummary(result: AllowlistPaste): string | null {
	if (result.added === 0 && result.duplicate === 0 && result.rejected === 0) return null;
	const parts: string[] = [];
	if (result.added > 0) parts.push(`Added ${result.added}.`);
	if (result.duplicate > 0) {
		parts.push(
			result.duplicate === 1 ? '1 was already listed.' : `${result.duplicate} were already listed.`
		);
	}
	if (result.rejected > 0) {
		parts.push(
			result.rejected === 1 ? '1 could not be read.' : `${result.rejected} could not be read.`
		);
	}
	return parts.join(' ');
}
