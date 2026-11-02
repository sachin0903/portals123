import config from '@graphene/config/eslint';

export default [
	...config,
	{
		files: ['**/*.svelte'],
		rules: {
			// These are passthrough component-library primitives using `...rest` props, never
			// compiled to custom elements — so valid-compile's custom-element prop-inference
			// warning is a false positive. Real compile *errors* still fail; svelte-check
			// (`ui:check`, via svelte.config's warningFilter) still surfaces other warnings.
			'svelte/valid-compile': ['error', { ignoreWarnings: true }],
			// `no-undef` can't see a component's `generics="T"` type parameter, so every generic
			// primitive (DataTable) reports its own type argument as undefined. typescript-eslint
			// turns this rule off for .ts for the same reason — TypeScript already catches
			// undefined identifiers, and here `ui:check` (svelte-check) is what enforces it.
			'no-undef': 'off'
		}
	}
];
