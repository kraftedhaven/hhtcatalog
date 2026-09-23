<script>
    import { onMount } from "svelte";

    let error = null;

    onMount(() => {
        const handleError = (event) => {
            error = {
                message: event.detail?.message || event.message || "An unexpected error occurred",
                stack: event.detail?.stack || event.stack || ""
            };
            console.error("Commerce Agent Error:", error);
        };

        window.addEventListener("commerce-agent-error", handleError);
        return () => window.removeEventListener("commerce-agent-error", handleError);
    });

    function resetError() {
        error = null;
    }
</script>

<div class="error-boundary">
    {#if error}
        <div class="error-container">
            <div class="error-icon">⚠️</div>
            <div class="error-content">
                <h2>Commerce Agent Error</h2>
                <p class="error-message">{error.message}</p>
                {#if error.stack}
                    <details class="error-details">
                        <summary>Stack trace</summary>
                        <pre>{error.stack}</pre>
                    </details>
                {/if}
                <button class="error-button" on:click={resetError}>
                    Try Again
                </button>
            </div>
        </div>
    {:else}
        <slot />
    {/if}
</div>

<style>
    .error-container {
        display: flex;
        gap: 16px;
        padding: 20px;
        background: #fee2e2;
        border: 1px solid #fca5a5;
        border-radius: 8px;
        margin: 20px 0;
    }

    .error-icon {
        font-size: 24px;
        flex-shrink: 0;
    }

    .error-content {
        flex: 1;
    }

    .error-content h2 {
        margin: 0 0 8px 0;
        color: #7f1d1d;
        font-size: 18px;
    }

    .error-message {
        margin: 0 0 12px 0;
        color: #991b1b;
        font-size: 14px;
    }

    .error-details {
        margin: 12px 0;
        cursor: pointer;
        color: #7f1d1d;
    }

    .error-details pre {
        background: rgba(0, 0, 0, 0.05);
        padding: 12px;
        border-radius: 4px;
        overflow-x: auto;
        font-size: 12px;
        margin: 8px 0 0 0;
    }

    .error-button {
        background: #dc2626;
        color: white;
        border: 0;
        border-radius: 6px;
        padding: 8px 16px;
        cursor: pointer;
        font-size: 14px;
    }

    .error-button:hover {
        background: #b91c1c;
    }
</style>
