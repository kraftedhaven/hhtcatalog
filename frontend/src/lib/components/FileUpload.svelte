<script>
  import { createEventDispatcher } from 'svelte';
  const dispatch = createEventDispatcher();

  let fileInput;

  function onFileChange(e) {
    const file = e.target.files && e.target.files[0];
    if (file) dispatch('fileselected', { file });
  }

  function triggerSelect() {
    fileInput.click();
  }
</script>

<div class="card">
  <label class="block text-sm font-medium text-slate-700">Upload image</label>
  <div class="mt-2 flex items-center gap-2">
    <input bind:this={fileInput} type="file" accept="image/*" on:change={onFileChange} class="hidden" />
    <button on:click={triggerSelect} class="px-4 py-2 bg-sky-600 text-white rounded hover:bg-sky-700">Choose File</button>
    <slot name="meta" />
  </div>
</div>
