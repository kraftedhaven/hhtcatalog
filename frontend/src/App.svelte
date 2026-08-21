<script>
    import "./app.css";
    import FileUpload from "$lib/components/FileUpload.svelte";
    import BulkUpload from "$lib/components/BulkUpload.svelte";
    import VisionResults from "$lib/components/VisionResults.svelte";
    import SKUDisplay from "$lib/components/SKUDisplay.svelte";
    import PricingDisplay from "$lib/components/PricingDisplay.svelte";
    import SEOListing from "$lib/components/SEOListing.svelte";
    import RawJSON from "$lib/components/RawJSON.svelte";
    import { analyzeImage, bulkAnalyze, downloadCSV, downloadJSON } from "$lib/api";

    let mode = "single"; // "single" | "bulk"
    let analysis = null;
    let bulk = null; // { count, demo, results }
    let loading = false;
    let error = null;
    let fileName = "";

    async function handleFileSelected(e) {
        error = null; analysis = null;
        const file = e.detail.file;
        if (!file) return;
        fileName = file.name;
        loading = true;
        try {
            analysis = await analyzeImage(file);
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function handleBulkSelected(e) {
        error = null; bulk = null;
        const files = e.detail.files;
        if (!files || !files.length) return;
        fileName = `${files.length} files`;
        loading = true;
        try {
            bulk = await bulkAnalyze(files);
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    $: bulkRows = bulk?.results || [];
    $: okCount = bulkRows.filter(r => r.status !== "error").length;
</script>

<div class="min-h-screen flex flex-col">
    <header class="bg-white border-b">
        <div class="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-sky-600 rounded flex items-center justify-center text-white font-bold">H</div>
                <div>
                    <h1 class="text-lg font-semibold">HHT Catalog — AI Dashboard</h1>
                    <p class="text-xs text-slate-500">Upload garment photos to generate SKU, pricing & SEO listings</p>
                </div>
            </div>
            <div class="flex items-center gap-1 bg-slate-100 rounded-lg p-1">
                <button on:click={() => (mode = "single")} class={`px-3 py-1.5 text-sm rounded-md ${mode === "single" ? "bg-white shadow text-slate-900" : "text-slate-600"}`}>Single</button>
                <button on:click={() => (mode = "bulk")} class={`px-3 py-1.5 text-sm rounded-md ${mode === "bulk" ? "bg-white shadow text-slate-900" : "text-slate-600"}`}>Bulk</button>
            </div>
        </div>
    </header>

    <main class="flex-1 max-w-6xl mx-auto px-4 py-6 w-full">
        {#if loading}
            <div class="text-sky-600 text-sm mb-4">Analyzing image(s)…</div>
        {:else if error}
            <div class="text-red-600 text-sm mb-4">{error}</div>
        {/if}

        {#if mode === "single"}
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-1 space-y-4">
                    <FileUpload on:fileselected={handleFileSelected}>
                        <div slot="meta" class="text-sm text-slate-600">{fileName}</div>
                    </FileUpload>
                    <div class="card">
                        <h3 class="text-lg font-semibold">Status</h3>
                        <div class="mt-2 text-sm">
                            {#if loading}<div class="text-sky-600">Analyzing image…</div>
                            {:else if error}<div class="text-red-600">{error}</div>
                            {:else if analysis}<div class="text-slate-700">Analysis complete {analysis.demo ? "(demo mode)" : ""}</div>
                            {:else}<div class="text-slate-500">Awaiting upload</div>{/if}
                        </div>
                    </div>
                    <RawJSON data={analysis} />
                </div>
                <div class="lg:col-span-2 space-y-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <VisionResults vision={analysis?.vision} />
                        <SKUDisplay sku={analysis?.sku ?? analysis} />
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <PricingDisplay pricing={analysis?.pricing} />
                        <SEOListing seo={analysis?.seo} />
                    </div>
                </div>
            </div>
        {:else}
            <div class="space-y-4">
                <BulkUpload on:filesselected={handleBulkSelected}>
                    <div slot="meta" class="text-sm text-slate-600">{fileName}</div>
                </BulkUpload>

                {#if bulk}
                    <div class="flex items-center justify-between">
                        <div class="text-sm text-slate-700">
                            {okCount} of {bulk.count} listings generated {bulk.demo ? "(demo mode)" : ""}
                        </div>
                        <div class="flex gap-2">
                            <button on:click={() => downloadCSV(bulkRows)} class="px-3 py-1.5 text-sm bg-emerald-600 text-white rounded hover:bg-emerald-700">Export CSV</button>
                            <button on:click={() => downloadJSON(bulkRows)} class="px-3 py-1.5 text-sm bg-slate-700 text-white rounded hover:bg-slate-800">Export JSON</button>
                        </div>
                    </div>

                    <div class="card overflow-x-auto">
                        <table class="min-w-full text-sm">
                            <thead class="bg-slate-50 text-slate-600 text-left">
                                <tr>
                                    <th class="px-3 py-2">File</th>
                                    <th class="px-3 py-2">Title</th>
                                    <th class="px-3 py-2">SKU</th>
                                    <th class="px-3 py-2 text-right">List</th>
                                    <th class="px-3 py-2 text-right">eBay</th>
                                    <th class="px-3 py-2 text-right">Depop</th>
                                    <th class="px-3 py-2">Platforms</th>
                                </tr>
                            </thead>
                            <tbody>
                                {#each bulkRows as r}
                                    <tr class="border-t border-slate-100">
                                        <td class="px-3 py-2 text-slate-500 max-w-[8rem] truncate" title={r.filename}>{r.filename}</td>
                                        <td class="px-3 py-2">{r.sku?.title ?? "—"}</td>
                                        <td class="px-3 py-2 font-mono text-xs">{r.sku?.code ?? r.error ?? "—"}</td>
                                        <td class="px-3 py-2 text-right">${r.pricing?.list_price ?? "—"}</td>
                                        <td class="px-3 py-2 text-right">${r.pricing?.ebay ?? "—"}</td>
                                        <td class="px-3 py-2 text-right">${r.pricing?.depop ?? "—"}</td>
                                        <td class="px-3 py-2 text-xs">{(r.seo?.platform_routing ?? []).join(", ")}</td>
                                    </tr>
                                {/each}
                            </tbody>
                        </table>
                    </div>
                {/if}
            </div>
        {/if}
    </main>

    <footer class="text-center text-sm text-slate-400 py-6">© {new Date().getFullYear()} HHT Catalog</footer>
</div>
