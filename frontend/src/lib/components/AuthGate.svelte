<script>
    import { createEventDispatcher, onMount } from "svelte";
    import { checkSellerAccess, loadAuthClient } from "$lib/neon-auth";

    const dispatch = createEventDispatcher();
    let auth;
    let sellerAccess = false;
    let email = "";
    let password = "";
    let mode = "sign-in";
    let session = null;
    let loading = true;
    let submitting = false;
    let message = "";
    let error = "";

    onMount(() => {
        void initialize();
    });

    async function initialize() {
        try {
            auth = await loadAuthClient();
            await refreshSession();
        } catch (cause) {
            error = cause.message;
        } finally {
            loading = false;
        }
    }

    async function refreshSession() {
        const result = await auth.getSession();
        if (result.error) throw new Error(result.error.message);
        session = result.data || null;
        sellerAccess = false;
        if (session) sellerAccess = await checkSellerAccess();
        dispatch("sessionChange", sellerAccess ? session : null);
    }

    async function submit() {
        error = "";
        message = "";
        submitting = true;
        try {
            const credentials = { email, password };
            const result = mode === "sign-in"
                ? await auth.signIn.email(credentials)
                : await auth.signUp.email({
                    ...credentials,
                    name: email.split("@")[0],
                    callbackURL: window.location.origin,
                });
            if (result.error) throw new Error(result.error.message);
            password = "";
            await refreshSession();
            if (mode === "sign-up" && !session) {
                message = "Check your email to confirm your account, then return here to sign in.";
            }
        } catch (cause) {
            error = cause.message;
        } finally {
            submitting = false;
        }
    }

    async function signOut() {
        error = "";
        try {
            const result = await auth.signOut();
            if (result.error) throw new Error(result.error.message);
            session = null;
            sellerAccess = false;
            dispatch("sessionChange", null);
        } catch (cause) {
            error = cause.message;
        }
    }
</script>

{#if loading}
    <main class="auth-shell"><p>Checking your session...</p></main>
{:else if !auth}
    <main class="auth-shell">
        <section class="auth-panel">
            <h1>HHT Catalog</h1>
            <p>{error}</p>
        </section>
    </main>
{:else if session}
    <div class="auth-session">
        <span>{session.user.email}</span>
        <button type="button" on:click={signOut}>Sign out</button>
    </div>
    {#if sellerAccess}
        {#if error}<p class="auth-error" role="alert">{error}</p>{/if}
        <slot />
    {:else}
        <main class="auth-shell">
            <section class="auth-panel">
                <h1>Seller access required</h1>
                <p>{error || "Verify your Neon account and have it linked to your existing HHT seller record."}</p>
                <button type="button" on:click={() => window.location.reload()}>Check access again</button>
            </section>
        </main>
    {/if}
{:else}
    <main class="auth-shell">
        <section class="auth-panel">
            <p class="eyebrow">HHT CATALOG</p>
            <h1>{mode === "sign-in" ? "Sign in" : "Create account"}</h1>
            <p>Use your approved account to access catalog tools and seller operations.</p>

            <form on:submit|preventDefault={submit}>
                <label>
                    Email
                    <input type="email" bind:value={email} autocomplete="email" required />
                </label>
                <label>
                    Password
                    <input type="password" bind:value={password} autocomplete={mode === "sign-in" ? "current-password" : "new-password"} minlength="8" required />
                </label>
                {#if error}<p class="auth-error">{error}</p>{/if}
                {#if message}<p class="auth-message">{message}</p>{/if}
                <button class="primary" type="submit" disabled={submitting}>
                    {submitting ? "Please wait..." : mode === "sign-in" ? "Sign in" : "Create account"}
                </button>
            </form>

            <button class="mode-toggle" type="button" disabled={submitting} on:click={() => {
                mode = mode === "sign-in" ? "sign-up" : "sign-in";
                error = "";
                message = "";
            }}>
                {mode === "sign-in" ? "Need an account? Create one" : "Already have an account? Sign in"}
            </button>
        </section>
    </main>
{/if}

<style>
    .auth-shell {
        display: grid;
        min-height: 100vh;
        place-items: center;
        padding: 24px;
    }

    .auth-panel {
        width: min(100%, 400px);
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--card);
        padding: 28px;
        box-shadow: 0 12px 28px rgba(19, 34, 56, .1);
    }

    .eyebrow {
        margin: 0 0 8px;
        color: var(--blue);
        font-size: 12px;
        font-weight: 800;
    }

    h1 { margin: 0; font-size: 25px; }
    .auth-panel > p:not(.eyebrow) { color: var(--muted); }
    form { display: grid; gap: 14px; margin-top: 24px; }
    label { display: grid; gap: 6px; color: #475569; font-size: 13px; font-weight: 700; }
    input { min-height: 42px; border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
    .auth-error, .auth-message { margin: 0; padding: 9px 10px; border-radius: 6px; font-size: 13px; }
    .auth-error { background: #fef2f2; color: var(--red); }
    .auth-message { background: #eff6ff; color: #1e3a8a; }
    .mode-toggle { width: 100%; margin-top: 12px; border: 0; color: var(--blue); background: transparent; }
    .auth-session { display: flex; align-items: center; justify-content: flex-end; gap: 10px; max-width: 1100px; margin: 10px auto -4px; padding: 0 14px; color: var(--muted); font-size: 13px; }
    .auth-session button { min-height: 32px; padding: 5px 9px; }
</style>
