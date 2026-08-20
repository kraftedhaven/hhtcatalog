import preprocess from 'svelte-preprocess';

/** Minimal Svelte config for Vite (not SvelteKit) */
const config = {
    preprocess: preprocess({ postcss: true })
};

export default config;
