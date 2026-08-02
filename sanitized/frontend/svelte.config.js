import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
	compilerOptions: {
		//Force runes mode for the project except for libraries, can be removed in svelte 6
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		//Adapter auto only supports some environments, see svelte docs for a list, if your environment is not supported switch out the adapter
		adapter: adapter()
	}
};

export default config;
