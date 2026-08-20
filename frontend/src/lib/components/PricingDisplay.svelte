<script>
  export let pricing = null;
</script>

<div class="card">
  <h3 class="text-lg font-semibold">Pricing</h3>
  {#if !pricing}
    <p class="text-sm text-slate-500">No pricing data.</p>
  {:else}
    <div class="mt-3 text-sm space-y-2">
      {#each Object.entries(pricing) as [k,v]}
        <div><strong>{k}:</strong> {v}</div>
      {/each}
    </div>
  {/if}
</div>
