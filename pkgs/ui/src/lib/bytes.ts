// Binary units (1024) labelled B/KB/MB/GB; one decimal from MB up. Zero is a real size (`0 B`), so
// a caller that stores "no size" as `0` passes `size || null`.

const KIB = 1024;

export function formatBytes(bytes: number | null | undefined, empty = '—'): string {
	if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return empty;
	if (bytes < KIB) return `${Math.round(bytes)} B`;
	const kib = bytes / KIB;
	if (kib < KIB) return `${Math.round(kib)} KB`;
	const mib = kib / KIB;
	if (mib < KIB) return `${mib.toFixed(1)} MB`;
	return `${(mib / KIB).toFixed(1)} GB`;
}
