<script>
  import '../app.css';
</script>

<div class="min-h-screen flex flex-col">
  <header class="bg-white border-b">
    <div class="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 bg-sky-600 rounded flex items-center justify-center text-white font-bold">H</div>
        <div>
          <h1 class="text-lg font-semibold">HHT Catalog — AI Dashboard</h1>
          <p class="text-xs text-slate-500">Upload an image to analyze SKU, pricing, SEO and vision outputs</p>
        </div>
      </div>
    </div>
  </header>

  <main class="flex-1 max-w-6xl mx-auto px-4 py-6 w-full">
    <slot />
  </main>

  <footer class="text-center text-sm text-slate-400 py-6">
    © {new Date().getFullYear()} HHT Catalog
  </footer>
</div>
