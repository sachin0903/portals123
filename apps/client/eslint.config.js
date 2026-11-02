import config from '@graphene/config/eslint';

export default [
	...config,
	// Vendored third-party (liquidGL) — not ours to lint; re-pull from upstream to update.
	{ ignores: ['src/lib/vendor/**'] }
];
