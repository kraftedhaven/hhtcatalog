<script>
  import { createEventDispatcher } from 'svelte';

  const dispatch = createEventDispatcher();
  let fileInput;

  function onFileChange(e) {
    const files = e.target.files && Array.from(e.target.files);
    if (files && files.length) dispatch('filesselected', { files });
    // reset so selecting the same files again re-triggers
    e.target.value = '';
  }

  function triggerSelect() {
    fileInput.click();
  }
</script>

<div class="card">
  <label class="block text-sm font-medium text-slate-700">Bulk upload images</label>
  <p class="text-xs text-slate-500 mt-1">Select multiple garment photos. Each becomes a listing.</p>
  <div class="mt-3 flex items-center gap-2">
    <input bind:this={fileInput} type="file" accept="image/*" multiple on:change={onFileChange} class="hidden" />
    <button on:click={triggerSelect} class="px-4 py-2 bg-sky-600 text-white rounded hover:bg-sky-700">Choose Files</button>
    <slot name="meta" />
  </div>
</div>
