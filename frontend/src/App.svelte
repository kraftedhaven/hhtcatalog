<script>
    import "./app.css";
    import FileUpload from "$lib/components/FileUpload.svelte";
    import VisionResults from "$lib/components/VisionResults.svelte";
    import RawJSON from "$lib/components/RawJSON.svelte";
    import { analyzeImage, downloadDraftJSON } from "$lib/api";

    let analysis = null;
    let draft = null;
    let loading = false;
    let error = null;
    let fileName = "";
    let copied = false;

    async function handleFileSelected(e) {
        error = null;
        analysis = null;
        draft = null;
        copied = false;
        const file = e.detail.file;
        if (!file) return;
        fileName = file.name;
        loading = true;
        try {
            analysis = await analyzeImage(file);
            draft = normalizeDraft(analysis?.draft);
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    function normalizeDraft(nextDraft) {
        const fallback = {
            title: "",
            description: "",
            condition: "good",
            price_suggestion: "",
            tags: [],
            sku: "",
        };
        const merged = { ...fallback, ...(nextDraft || {}) };
        if (Array.isArray(merged.tags)) {
            merged.tagsText = merged.tags.join(", ");
        } else {
            merged.tagsText = String(merged.tags || "");
        }
        return merged;
    }

    $: reviewedDraft = draft
        ? {
              title: draft.title,
              description: draft.description,
              condition: draft.condition,
              price_suggestion: Number(draft.price_suggestion) || draft.price_suggestion,
              tags: draft.tagsText.split(",").map((tag) => tag.trim()).filter(Boolean),
              sku: draft.sku,
          }
        : null;

    async function copyDraft() {
        if (!reviewedDraft) return;
        await navigator.clipboard.writeText(JSON.stringify(reviewedDraft, null, 2));
        copied = true;
        setTimeout(() => (copied = false), 1800);
    }
</script>

<div class="min-h-screen flex flex-col">
    <header class="bg-white border-b">
        <div class="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="brand-mark">H</div>
                <div>
                    <h1 class="text-lg font-semibold">HHT Catalog Drafts</h1>
                    <p class="text-xs text-slate-500">Analyze images and review editable marketplace listing drafts</p>
                </div>
            </div>
            <span class="review-pill">Review required</span>
        </div>
    </header>

    <main class="flex-1 max-w-6xl mx-auto px-4 py-6 w-full">
        {#if loading}
            <div class="notice info">Analyzing image...</div>
        {:else if error}
            <div class="notice error">{error}</div>
        {/if}

        <div class="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
            <aside class="space-y-4">
                <FileUpload on:fileselected={handleFileSelected}>
                    <div slot="meta" class="text-sm text-slate-600 truncate">{fileName}</div>
                </FileUpload>
                <div class="card">
                    <h2 class="panel-title">Status</h2>
                    <div class="mt-2 text-sm">
                        {#if loading}<div class="text-sky-700">Analyzing image...</div>
                        {:else if error}<div class="text-red-700">{error}</div>
                        {:else if analysis}<div class="text-slate-700">Draft generated {analysis.demo ? "(demo mode)" : ""}</div>
                        {:else}<div class="text-slate-500">Awaiting upload</div>{/if}
                    </div>
                </div>
                <VisionResults vision={analysis?.vision} />
                <RawJSON data={analysis} />
            </aside>

            <section class="card">
                <div class="draft-header">
                    <div>
                        <h2 class="panel-title">Marketplace Listing Draft</h2>
                        <p class="text-sm text-slate-500">Edit before copying or exporting. Nothing is published automatically.</p>
                    </div>
                    <div class="flex gap-2">
                        <button class="secondary-button" disabled={!reviewedDraft} on:click={() => downloadDraftJSON(reviewedDraft)}>Export</button>
                        <button class="primary-button" disabled={!reviewedDraft} on:click={copyDraft}>{copied ? "Copied" : "Copy"}</button>
                    </div>
                </div>

                {#if !draft}
                    <div class="empty-state">
                        <h3 class="text-base font-semibold text-slate-700">Upload a product image to start</h3>
                        <p class="text-sm text-slate-500">The app will return title, description, condition, price suggestion, tags, and SKU as an editable draft.</p>
                    </div>
                {:else}
                    <form class="draft-form" on:submit|preventDefault>
                        <label>
                            <span>Title</span>
                            <input bind:value={draft.title} maxlength="120" />
                        </label>

                        <label>
                            <span>Description</span>
                            <textarea bind:value={draft.description} rows="8"></textarea>
                        </label>

                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <label>
                                <span>Condition</span>
                                <select bind:value={draft.condition}>
                                    <option value="new">New</option>
                                    <option value="likenew">Like new</option>
                                    <option value="excellent">Excellent</option>
                                    <option value="verygood">Very good</option>
                                    <option value="good">Good</option>
                                    <option value="fair">Fair</option>
                                </select>
                            </label>

                            <label>
                                <span>Price Suggestion</span>
                                <input bind:value={draft.price_suggestion} inputmode="decimal" />
                            </label>

                            <label>
                                <span>SKU</span>
                                <input bind:value={draft.sku} />
                            </label>
                        </div>

                        <label>
                            <span>Tags</span>
                            <input bind:value={draft.tagsText} />
                        </label>
                    </form>
                {/if}
            </section>
        </div>
    </main>

    <footer class="text-center text-sm text-slate-400 py-6">HHT Catalog</footer>
</div>
