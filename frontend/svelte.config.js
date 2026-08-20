import preprocess from 'svelte-preprocess';

/** Minimal Svelte config for Vite (not SvelteKit) */
const config = {
    preprocess: preprocess({ postcss: true }),
    compilerOptions: {
        // Keep the old `new Component()` API compatibility for existing code
        compat: {
            componentApi: 4
        }
    }
};

export default config;
