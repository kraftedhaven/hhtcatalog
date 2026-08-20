<script>
    import FileUpload from "$lib/components/FileUpload.svelte";
    import VisionResults from "$lib/components/VisionResults.svelte";
    import SKUDisplay from "$lib/components/SKUDisplay.svelte";
    import PricingDisplay from "$lib/components/PricingDisplay.svelte";
    import SEOListing from "$lib/components/SEOListing.svelte";
    import RawJSON from "$lib/components/RawJSON.svelte";
    import { analyzeImage } from "$lib/api";

    let analysis = null;
    let loading = false;
    let error = null;
    let fileName = "";

    async function handleFileSelected(e) {
        error = null;
        analysis = null;
        const file = e.detail.file;
        if (!file) return;
        fileName = file.name;
        loading = true;
        try {
            const data = await analyzeImage(file);
            // Expect the backend to return a full pipeline object. We map gracefully.
            analysis = data;
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }
</script>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
    <div class="lg:col-span-1 space-y-4">
        <FileUpload on:fileselected={handleFileSelected}>
            <div slot="meta" class="text-sm text-slate-600">{fileName}</div>
        </FileUpload>

        <div class="card">
            <h3 class="text-lg font-semibold">Status</h3>
            <div class="mt-2 text-sm">
                {#if loading}
                    <div class="text-sky-600">Analyzing image…</div>
                {:else if error}
                    <div class="text-red-600">{error}</div>
                {:else if analysis}
                    <div class="text-slate-700">Analysis complete</div>
                {:else}
                    <div class="text-slate-500">Awaiting upload</div>
                {/if}
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
