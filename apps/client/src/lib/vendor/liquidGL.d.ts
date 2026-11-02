// Types for the vendored liquidGL globals (the script attaches to `window`).
export {};

declare global {
	interface Window {
		html2canvas?: unknown;
		liquidGL?: (opts: Record<string, unknown>) => unknown;
		__liquidGLRenderer__?: { _rafId?: number; canvas?: HTMLCanvasElement };
		__liquidGLNoWebGL__?: boolean;
	}
}
