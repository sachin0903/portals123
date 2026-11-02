// Shared ESLint flat config. Apps import this and append their own ignores.
import js from '@eslint/js';
import ts from 'typescript-eslint';
import svelte from 'eslint-plugin-svelte';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

/** @type {import('eslint').Linter.Config[]} */
export default [
	js.configs.recommended,
	...ts.configs.recommended,
	...svelte.configs['flat/recommended'],
	prettier,
	...svelte.configs['flat/prettier'],
	{
		languageOptions: {
			globals: { ...globals.browser, ...globals.node }
		}
	},
	{
		files: ['**/*.svelte'],
		languageOptions: {
			parserOptions: { parser: ts.parser }
		}
	},
	{
		rules: {
			// Empty interfaces are idiomatic in SvelteKit's app.d.ts (declaration merging).
			'@typescript-eslint/no-empty-object-type': 'off'
		}
	},
	{
		ignores: ['build/', '.svelte-kit/', 'dist/', 'src/lib/api/**', '**/*.timestamp-*.mjs']
	}
];
