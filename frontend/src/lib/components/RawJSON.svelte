<script>
  export let data = null;
</script>

<div class="card">
  <h3 class="text-lg font-semibold">Raw JSON</h3>
  {#if !data}
    <p class="text-sm text-slate-500">No raw data.</p>
  {:else}
    <pre class="mt-2 text-xs bg-slate-50 p-3 rounded overflow-auto">{JSON.stringify(data, null, 2)}</pre>
  {/if}
</div>
