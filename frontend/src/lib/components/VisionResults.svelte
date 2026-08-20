<script>
  export let vision = null;
</script>

<div class="card">
  <h3 class="text-lg font-semibold">Vision Results</h3>
  {#if !vision}
    <p class="text-sm text-slate-500">No vision data yet.</p>
  {:else}
    <div class="mt-3 space-y-2 text-sm">
      {#if vision.labels}
        <div>
          <strong>Labels:</strong>
          <div class="mt-1 flex flex-wrap gap-2">
            {#each vision.labels as l}
              <span class="px-2 py-1 bg-slate-100 rounded">{l}</span>
            {/each}
          </div>
        </div>
      {/if}

      {#if vision.colors}
        <div>
          <strong>Colors:</strong>
          <div class="mt-1 flex gap-2 items-center">
            {#each vision.colors as c}
              <div class="w-8 h-6 rounded" style="background:{c}"></div>
            {/each}
          </div>
        </div>
      {/if}

      {#if vision.text}
        <div>
          <strong>Detected text:</strong>
          <pre class="whitespace-pre-wrap bg-slate-50 p-2 rounded mt-1">{vision.text}</pre>
        </div>
      {/if}
    </div>
  {/if}
</div>
