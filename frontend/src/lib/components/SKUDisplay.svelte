<script>
  export let sku = null;
</script>

<div class="card">
  <h3 class="text-lg font-semibold">SKU / Product</h3>
  {#if !sku}
    <p class="text-sm text-slate-500">No SKU generated yet.</p>
  {:else}
    <div class="mt-3 text-sm space-y-1">
      <div><strong>Title:</strong> {sku.title}</div>
      <div><strong>Category:</strong> {sku.category}</div>
      <div><strong>Condition:</strong> {sku.condition_id}</div>
      <div class="break-words"><strong>Description:</strong> {sku.description}</div>
    </div>
  {/if}
</div>
