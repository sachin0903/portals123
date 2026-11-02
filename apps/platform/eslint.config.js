import config from '@graphene/config/eslint';

export default [
	...config,
	{
		// Vendored GrapesJS canvas helper scripts (copied verbatim, run inside the
		// editor iframe) — not our source, don't lint them.
		ignores: ['static/grapesjs-*.js']
	}
];
