<script>
  export let seo = null;
</script>

<div class="card">
  <h3 class="text-lg font-semibold">SEO Listing</h3>
  {#if !seo}
    <p class="text-sm text-slate-500">No SEO suggestions yet.</p>
  {:else}
    <div class="mt-3 text-sm space-y-2">
      <div><strong>Title Suggestion:</strong> {seo.title}</div>
      <div><strong>Meta Description:</strong> {seo.meta_description}</div>
      {#if seo.keywords}
        <div><strong>Keywords:</strong> {seo.keywords.join(', ')}</div>
      {/if}
    </div>
  {/if}
</div>
