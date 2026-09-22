<script>
    import "./app.css";
    import { analyzeImages, downloadCSV, downloadDraftCSV, downloadJSON, ebayCategoryAspects, ebayCategorySuggestions, ebayOAuthStart, ebayOAuthStatus, sendDraftFeed } from "$lib/api";
    import { applyClientItemRules, CATEGORY_OPTIONS, EMPTY_ITEM } from "$lib/ebay";
    import CommerceAgent from "$lib/components/CommerceAgent.svelte";

    const emptyItem = EMPTY_ITEM;
    const defaultSeller = {
        location: "Kettering, Ohio",
        postalCode: "45429",
        countryCode: "US",
        paymentProfileName: "eBay Payments",
        shippingProfileName: "Standard Shipping",
        returnProfileName: "30 Day Returns",
        dispatchTimeMax: "3"
    };

    let tab = "analyze";
    let engine = "hosted";
    let files = [];
    let previews = [];
    let item = load("hht_current_item", emptyItem);
    let queue = load("hht_queue", []);
    let seller = load("hht_seller_defaults", defaultSeller);
    let status = "";
    let error = "";
    let loading = false;
    let canTryAlternate = false;
    let alternateProvider = "";
    let draftLoading = false;
    let restoreInput;
    let localPipeline = null;
    let categoryQuery = "";
    let categorySuggestions = [];
    let categoryFields = [];
    let categoryLoading = false;
    let categoryNotice = "";
    let oauthLoading = false;
    let oauthStatus = "";

    $: titleLength = (item.title || "").length;
    $: queueTotal = queue.reduce((sum, next) => sum + (Number.parseFloat(next.price) || 0), 0);
    $: queueAverage = queue.length ? queueTotal / queue.length : 0;
    $: persist("hht_queue", queue);
    $: persist("hht_seller_defaults", seller);
    $: persist("hht_current_item", item);
    $: reviewNotes = sellerReviewNotes(item);

    function load(key, fallback) {
        try {
            const parsed = JSON.parse(localStorage.getItem(key) || "null");
            if (Array.isArray(fallback)) return Array.isArray(parsed) ? parsed : [];
            return { ...fallback, ...(parsed || {}) };
        } catch {
            return Array.isArray(fallback) ? [] : { ...fallback };
        }
    }

    function persist(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    }

    async function onFilesSelected(event) {
        const chosen = Array.from(event.target.files || []).filter((file) => file.type.startsWith("image/")).slice(0, 5);
        await setFiles(chosen);
    }

    async function setFiles(nextFiles) {
        files = nextFiles;
        previews.forEach((preview) => URL.revokeObjectURL(preview.url));
        previews = files.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
        status = files.length ? `${files.length} photo${files.length === 1 ? "" : "s"} ready.` : "";
        error = files.length ? "" : "Choose one to five image files.";
        canTryAlternate = false;
        alternateProvider = "";
    }

    function removeFile(index) {
        const next = files.slice();
        next.splice(index, 1);
        setFiles(next);
    }

    async function analyze(options = {}) {
        error = "";
        canTryAlternate = false;
        alternateProvider = "";
        if (!files.length) {
            error = "Upload at least one item photo first.";
            return;
        }
        loading = true;
        try {
            status = engine === "hosted" ? "Compressing and uploading photos..." : "Starting browser-local model...";
            const hostedFiles = engine === "hosted" ? await compactHostedFiles(files) : files;
            status = engine === "hosted" ? "Uploading compressed photos for secure analysis..." : status;
            const result = engine === "hosted" ? await analyzeImages(hostedFiles, seller, options) : await localAnalyze();
            item = normalizeForForm(result);
            status = result.demo ? "Demo result loaded. Review required." : `Analysis complete via ${result.provider || engine}. Review required.`;
            tab = "edit";
        } catch (err) {
            if (engine === "hosted" && err.canTryAlternate && !options.tryAlternate) {
                status = `Primary vision provider unavailable. Retrying with ${err.alternateProvider || "the alternate provider"}...`;
                await analyze({ ...options, tryAlternate: true });
                return;
            }
            error = friendlyAnalyzeError(err);
            canTryAlternate = engine === "hosted" && Boolean(err.canTryAlternate) && !options.tryAlternate;
            alternateProvider = canTryAlternate ? (err.alternateProvider || "") : "";
            status = "";
        } finally {
            loading = false;
        }
    }

    async function localAnalyze() {
        status = "Loading SmolVLM in this browser. This may be slow or unsupported on some phones.";
        const mod = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1");
        if (!localPipeline) {
            localPipeline = await mod.pipeline("image-text-to-text", "HuggingFaceTB/SmolVLM-256M-Instruct", { device: "webgpu", dtype: "q4" });
        }
        const images = await Promise.all(files.map(fileToDataUrl));
        const content = images.map((url) => ({ type: "image", url }));
        content.push({ type: "text", text: "Inspect every clothing, shoe, or bag photo and return JSON keys title, price, cid, cnote, cat, brand, size, color, dept, type, style, mat, pat, slv, nk, sea, occ, st, vin, desc, notes, madeIn, serialNumber, measurements. Use Not visible rather than guessing." });
        const output = await localPipeline([{ role: "user", content }], { max_new_tokens: 1200 });
        return normalizeForForm(parseModelJSON(JSON.stringify(output)));
    }

    function fileToDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    async function compactHostedFiles(sourceFiles) {
        const resized = [];
        for (const file of sourceFiles.slice(0, 3)) {
            resized.push(await resizeImage(file));
        }
        return resized;
    }

    function resizeImage(file) {
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => {
                const image = new Image();
                image.onload = () => {
                    const max = 1600;
                    const scale = Math.min(1, max / Math.max(image.width, image.height));
                    const canvas = document.createElement("canvas");
                    canvas.width = Math.max(1, Math.round(image.width * scale));
                    canvas.height = Math.max(1, Math.round(image.height * scale));
                    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
                    canvas.toBlob((blob) => {
                        if (!blob) {
                            resolve(file);
                            return;
                        }
                        resolve(new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), { type: "image/jpeg" }));
                    }, "image/jpeg", 0.82);
                };
                image.onerror = () => resolve(file);
                image.src = reader.result;
            };
            reader.onerror = () => resolve(file);
            reader.readAsDataURL(file);
        });
    }

    async function makeContactSheets(sourceFiles) {
        const dataUrls = await Promise.all(sourceFiles.map(fileToDataUrl));
        const groups = [dataUrls.slice(0, 3), dataUrls.slice(3)];
        const sheets = [];
        for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
            const group = groups[groupIndex].filter(Boolean);
            if (!group.length) continue;
            sheets.push(await drawContactSheet(group, groupIndex));
        }
        return sheets;
    }

    function drawContactSheet(dataUrls, groupIndex) {
        return new Promise((resolve) => {
            const tile = 720;
            const cols = dataUrls.length === 1 ? 1 : 2;
            const rows = Math.ceil(dataUrls.length / cols);
            const canvas = document.createElement("canvas");
            canvas.width = cols * tile;
            canvas.height = rows * tile;
            const ctx = canvas.getContext("2d");
            ctx.fillStyle = "#f8fafc";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            let done = 0;
            dataUrls.forEach((url, index) => {
                const image = new Image();
                image.onload = () => {
                    const scale = Math.min((tile - 36) / image.width, (tile - 56) / image.height);
                    const width = image.width * scale;
                    const height = image.height * scale;
                    const left = (index % cols) * tile + (tile - width) / 2;
                    const top = Math.floor(index / cols) * tile + 40 + (tile - 56 - height) / 2;
                    ctx.drawImage(image, left, top, width, height);
                    ctx.fillStyle = "#0f172a";
                    ctx.font = "bold 26px system-ui, sans-serif";
                    ctx.fillText(`Photo ${groupIndex * 3 + index + 1}`, (index % cols) * tile + 18, Math.floor(index / cols) * tile + 32);
                    done += 1;
                    if (done === dataUrls.length) {
                        canvas.toBlob((blob) => {
                            resolve(blob ? new File([blob], `contact-sheet-${groupIndex + 1}.jpg`, { type: "image/jpeg" }) : new File([], `contact-sheet-${groupIndex + 1}.jpg`, { type: "image/jpeg" }));
                        }, "image/jpeg", 0.82);
                    }
                };
                image.onerror = () => {
                    done += 1;
                    if (done === dataUrls.length) {
                        canvas.toBlob((blob) => resolve(new File([blob], `contact-sheet-${groupIndex + 1}.jpg`, { type: "image/jpeg" })), "image/jpeg", 0.82);
                    }
                };
                image.src = url;
            });
        });
    }

    function friendlyAnalyzeError(err) {
        const failures = err.providerFailures || [];
        if (!failures.length) return err.message || String(err);
        const labels = {
            authentication: "API key authentication failed",
            permission: "model or account access problem",
            not_found: "endpoint or model was not found",
            payload_too_large: "image upload is too large after compression",
            rate_limit: "rate limit or free model unavailable",
            rate_limited: "Z.AI rate limit reached; wait a few minutes and retry one small photo",
            request_error: "request parameters were rejected",
            server_error: "provider server error",
            malformed_json: "provider returned unreadable JSON",
            non_vision_model: "configured model did not return a vision analysis",
            transport: "provider connection failed",
            timeout: "provider timed out",
            provider_error: "provider request failed",
            timeout_budget_exhausted: "skipped to avoid Heroku timeout",
            invalid_request: "invalid upload request"
        };
        const lines = failures.map((failure) => {
            const category = failure.category || failure.error;
            const label = failure.message || labels[category] || category || "failed";
            const status = failure.status || failure.httpStatus;
            return `${failure.provider}: ${label}${status ? ` (${status})` : ""}`;
        });
        return `${err.message || "Analysis failed."} ${lines.join("; ")}. No demo data was shown.`;
    }

    function friendlyEbayError(err) {
        const failures = err.providerFailures || [];
        if (!failures.length) return err.message || "eBay draft creation failed.";
        const failure = failures[0];
        const statusText = failure.status ? ` (${failure.status})` : "";
        return `${failure.provider || "eBay"}: ${failure.message || failure.category || "draft creation failed"}${statusText}`;
    }

    function parseModelJSON(raw) {
        const text = String(raw || "").replace(/```json|```/gi, "");
        const start = text.indexOf("{");
        const end = text.lastIndexOf("}");
        if (start < 0 || end <= start) throw new Error("The local model did not return JSON. Try hosted secure mode.");
        return JSON.parse(text.slice(start, end + 1));
    }

    function normalizeForForm(result) {
        return applyClientRules({ ...emptyItem, ...result, price: result.price || "" });
    }

    function applyClientRules(nextItem = item) {
        return applyClientItemRules(nextItem);
    }

    async function findCategories(query = categoryQuery || item.title || `${item.brand} ${item.type}`) {
        const search = String(query || "").trim();
        if (search.length < 2) {
            categorySuggestions = [];
            categoryNotice = "Enter at least two words or characters to search eBay categories.";
            return;
        }
        categoryLoading = true;
        categoryNotice = "Searching live eBay category suggestions...";
        try {
            const result = await ebayCategorySuggestions(search);
            categorySuggestions = result.suggestions || [];
            categoryNotice = result.message || (categorySuggestions.length ? "Choose the best matching eBay leaf category." : "No category suggestions found. Try a more specific item type.");
        } catch (err) {
            categorySuggestions = [];
            categoryNotice = err.message || "eBay category suggestions are unavailable.";
        } finally {
            categoryLoading = false;
        }
    }

    async function loadCategoryFields(categoryId = item.cat) {
        const id = String(categoryId || "").trim();
        if (!id) {
            categoryFields = [];
            return;
        }
        categoryLoading = true;
        try {
            const result = await ebayCategoryAspects(id);
            categoryFields = result.fields || [];
            categoryNotice = result.message || "";
        } catch (err) {
            categoryFields = [];
            categoryNotice = err.message || "eBay category fields are unavailable.";
        } finally {
            categoryLoading = false;
        }
    }

    async function chooseCategory(suggestion) {
        item = applyClientRules({
            ...item,
            cat: suggestion.categoryId,
            categoryName: suggestion.path || suggestion.categoryName,
            itemSpecifics: { ...(item.itemSpecifics || {}) },
        });
        categoryQuery = suggestion.path || suggestion.categoryName;
        categorySuggestions = [];
        await loadCategoryFields(suggestion.categoryId);
    }

    function updateCategoryField(name, value) {
        item = {
            ...item,
            itemSpecifics: { ...(item.itemSpecifics || {}), [name]: value },
        };
    }

    function categoryFieldValue(name) {
        return item.itemSpecifics?.[name] || "";
    }

    function reviewedCandidate(source = item) {
        let reviewed = applyClientRules(source);
        if (!reviewed.desc) reviewed = { ...reviewed, desc: description(reviewed) };
        return reviewed;
    }

    function firstInvalidQueuedItem(source = queue) {
        for (let index = 0; index < source.length; index += 1) {
            const reviewed = reviewedCandidate(source[index]);
            const validation = validateItem(reviewed);
            if (validation) return { index, reviewed, message: validation };
        }
        return null;
    }

    function addToQueue() {
        const reviewed = reviewedCandidate(item);
        const validation = validateItem(reviewed);
        if (validation) {
            error = validation;
            tab = "edit";
            return;
        }
        queue = [...queue, reviewed];
        item = { ...emptyItem };
        status = "Item added to queue.";
        tab = "queue";
    }

    function validateItem(candidate) {
        if (!candidate.title || candidate.title.length > 80) return "Title is required and must be 80 characters or fewer.";
        if (!candidate.price || Number.parseFloat(candidate.price) <= 0) return "Enter a positive fixed price.";
        if (!candidate.cat) return "Choose a supplied eBay category before queueing.";
        if (!candidate.brand) return "Brand is required. Use Not visible or No Brand if needed.";
        if (["3000", "5000", "6000"].includes(candidate.cid) && !candidate.cnote) return "Add a condition note for used condition codes.";
        return "";
    }

    function editQueued(index) {
        item = { ...queue[index] };
        queue = queue.filter((_, i) => i !== index);
        tab = "edit";
    }

    async function exportQueue() {
        if (!queue.length) {
            error = "Queue is empty.";
            return;
        }
        const invalid = firstInvalidQueuedItem();
        if (invalid) {
            error = `Queue item ${invalid.index + 1}: ${invalid.message}`;
            item = { ...invalid.reviewed };
            tab = "edit";
            return;
        }
        try {
            await downloadCSV(queue, seller);
        } catch (err) {
            error = err.message || String(err);
        }
    }

    async function exportDraftQueue() {
        if (!queue.length) {
            error = "Queue is empty.";
            return;
        }
        const invalid = firstInvalidQueuedItem();
        if (invalid) {
            error = `Queue item ${invalid.index + 1}: ${invalid.message}`;
            item = { ...invalid.reviewed };
            tab = "edit";
            return;
        }
        try {
            await downloadDraftCSV(queue);
        } catch (err) {
            error = err.message || String(err);
        }
    }

    async function sendDraftQueue() {
        if (!queue.length) {
            error = "Queue is empty.";
            return;
        }
        const invalid = firstInvalidQueuedItem();
        if (invalid) {
            error = `Queue item ${invalid.index + 1}: ${invalid.message}`;
            item = { ...invalid.reviewed };
            tab = "edit";
            return;
        }
        const confirmed = window.confirm("Send this queue to eBay as a Seller Hub draft feed? This submits drafts for processing; it does not publish live listings.");
        if (!confirmed) return;
        error = "";
        draftLoading = true;
        try {
            const result = await sendDraftFeed(queue);
            queue = queue.map((entry) => ({ ...entry, ebayFeedTaskId: result.taskId, ebayDraftStatus: result.status }));
            status = `Seller Hub draft feed submitted to eBay. Task ${result.taskId}; ${result.itemCount} item${result.itemCount === 1 ? "" : "s"} sent. Check Seller Hub Reports for processing results.`;
        } catch (err) {
            error = friendlyEbayError(err);
        } finally {
            draftLoading = false;
        }
    }

    async function checkEbayConnection() {
        error = "";
        oauthLoading = true;
        try {
            const result = await ebayOAuthStatus();
            oauthStatus = result.configured ? "eBay OAuth is connected." : "eBay OAuth is not connected.";
        } catch (err) {
            oauthStatus = "";
            error = err.message || "Unable to check eBay OAuth status.";
        } finally {
            oauthLoading = false;
        }
    }

    async function reconnectEbay() {
        error = "";
        oauthLoading = true;
        try {
            const result = await ebayOAuthStart();
            oauthStatus = "Opening eBay authorization...";
            window.location.href = result.authorizationUrl;
        } catch (err) {
            oauthStatus = "";
            error = err.message || "Unable to start eBay reconnect.";
            oauthLoading = false;
        }
    }

    function backupQueue() {
        downloadJSON({ queue, seller }, "hht-listings-backup.json");
    }

    async function restoreBackup(event) {
        const file = event.target.files?.[0];
        if (!file) return;
        const text = await file.text();
        const data = JSON.parse(text);
        queue = Array.isArray(data.queue) ? data.queue : [];
        seller = { ...defaultSeller, ...(data.seller || {}) };
        status = "Backup restored.";
        tab = "queue";
    }

    function description(source = item) {
        const esc = (value) => String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        const rows = [
            ["Brand", source.brand], ["Size", source.size], ["Color", source.color],
            ["Department", source.dept], ["Type", source.type], ["Style", source.style],
            ["Material", source.mat], ["Pattern", source.pat], ["Condition", source.cnote],
            ["Made In label", source.madeIn], ["Interior patch / serial", source.serialNumber],
            ["Measurements", source.measurements]
        ].filter(([, value]) => value);
        return `<p><strong>${esc(source.title)}</strong></p><ul>${rows.map(([label, value]) => `<li><strong>${esc(label)}:</strong> ${esc(value)}</li>`).join("")}</ul><p>Ships from ${esc(seller.location)}. 30-day returns accepted.</p>`;
    }

    function sellerReviewNotes(source) {
        const notes = [];
        if (source.notes) notes.push(source.notes);
        if (/gucci/i.test(source.brand || "") && source.madeIn && !/italy|italia/i.test(source.madeIn)) {
            notes.push("Gucci origin conflict requires independent verification. Do not claim authenticity from photos.");
        }
        if (/gucci|louis vuitton|chanel|prada|fendi|hermes|versace|burberry|coach|michael kors|tory burch|balenciaga|dior|saint laurent|ysl/i.test(source.brand || "")) {
            notes.push("Luxury-brand item requires seller review. Preserve exact Made In and serial wording.");
        }
        return [...new Set(notes.filter(Boolean))];
    }

</script>

<div class="shell">
    <header class="topbar">
        <div>
            <h1>HHT eBay Listing Builder</h1>
            <p>Photo analysis, seller review, and Seller Hub draft feed submission</p>
        </div>
        <strong>{queue.length} item{queue.length === 1 ? "" : "s"}</strong>
    </header>

    <nav class="tabs" aria-label="Main navigation">
        <button class:on={tab === "commerce"} on:click={() => tab = "commerce"}>Commerce Agent</button>
        <button class:on={tab === "analyze"} on:click={() => tab = "analyze"}>Analyze</button>
        <button class:on={tab === "edit"} on:click={() => tab = "edit"}>Edit</button>
        <button class:on={tab === "queue"} on:click={() => tab = "queue"}>Queue</button>
        <button class:on={tab === "settings"} on:click={() => tab = "settings"}>Settings</button>
    </nav>

    {#if error}
        <div class="notice error">
            <p>{error}</p>
            {#if canTryAlternate}
                <button type="button" class="mt-2 underline" disabled={loading} on:click={() => analyze({ tryAlternate: true })}>
                    Try alternate provider{alternateProvider ? ` (${alternateProvider})` : ""}
                </button>
            {/if}
        </div>
    {/if}
    {#if status}<div class="notice info">{status}</div>{/if}

    {#if tab === "analyze"}
        <section class="panel">
            <label class="field">
                <span>Analysis engine</span>
                <select bind:value={engine}>
                    <option value="hosted">Hosted vision with automatic fallback</option>
                    <option value="local">Browser-local SmolVLM experimental</option>
                </select>
            </label>
            <p class="help">
                {engine === "hosted"
                    ? "Photos go to this Heroku app. It uses the primary provider and automatically retries with the configured alternate provider if the primary is temporarily unavailable."
                    : "The browser downloads an open-source model locally. It may be slow or unsupported on phones."}
            </p>
            <label class="dropzone">
                <input type="file" accept="image/*" multiple on:change={onFilesSelected} />
                <strong>Choose 1-5 item photos</strong>
                <span>Camera or photo library. Review every AI result before export.</span>
            </label>
            {#if previews.length}
                <div class="preview-grid">
                    {#each previews as preview, index}
                        <div class="thumb">
                            <img src={preview.url} alt={preview.name} />
                            <button type="button" on:click={() => removeFile(index)} aria-label="Remove photo">x</button>
                        </div>
                    {/each}
                </div>
            {/if}
            <div class="actions">
                <button class="primary" disabled={loading || !files.length} on:click={analyze}>{loading ? "Analyzing..." : "Analyze photos"}</button>
                <button type="button" on:click={() => setFiles([])}>Clear photos</button>
            </div>
        </section>
    {/if}

    {#if tab === "commerce"}
        <CommerceAgent />
    {/if}

    {#if tab === "edit"}
        {#if reviewNotes.length}
            <div class="notice warn">
                <strong>Seller review required</strong>
                {#each reviewNotes as note}<p>{note}</p>{/each}
            </div>
        {/if}
        <section class="panel form">
            <label class="field wide"><span>Title <em>{titleLength}/80</em></span><input bind:value={item.title} maxlength="80" /></label>
            <label class="field"><span>Price</span><input bind:value={item.price} inputmode="decimal" /></label>
            <label class="field"><span>SKU / Custom label</span><input bind:value={item.sku} placeholder="Optional unique item code" /></label>
            <label class="field"><span>Quantity</span><input bind:value={item.quantity} inputmode="numeric" placeholder="1" /></label>
            <label class="field"><span>UPC / GTIN</span><input bind:value={item.upc} placeholder="Leave blank if not available" /></label>
            <label class="field"><span>Condition ID (cid)</span><select bind:value={item.cid}><option value="1000">1000 New with Tags</option><option value="1500">1500 New without Tags</option><option value="3000">3000 Pre-Owned</option><option value="4000">4000 Very Good</option><option value="5000">5000 Good</option><option value="6000">6000 Acceptable</option></select></label>
            <div class="wide category-panel">
                <div class="category-head">
                    <div>
                        <strong>eBay category and required fields</strong>
                        <p>Search live eBay categories by item type; select a leaf category to load eBay’s current required and recommended item specifics.</p>
                    </div>
                    {#if item.cat}<span>Selected ID: {item.cat}</span>{/if}
                </div>
                <div class="category-search">
                    <input bind:value={categoryQuery} placeholder="Search: kids hat, Coach bag, women’s sandals" on:keydown={(event) => event.key === "Enter" && (event.preventDefault(), findCategories())} />
                    <button type="button" disabled={categoryLoading} on:click={() => findCategories()}>{categoryLoading ? "Searching..." : "Find eBay categories"}</button>
                </div>
                <label class="field"><span>Quick category menu</span><select bind:value={item.cat} on:change={() => { item = applyClientRules(item); loadCategoryFields(item.cat); }}>{#each CATEGORY_OPTIONS as option}<option value={option.value}>{option.label}</option>{/each}</select></label>
                <div class="category-manual">
                    <label class="field"><span>Manual eBay category ID</span><input bind:value={item.cat} inputmode="numeric" placeholder="Use only a verified eBay leaf category ID" on:change={() => loadCategoryFields(item.cat)} /></label>
                    <button type="button" disabled={categoryLoading || !item.cat} on:click={() => loadCategoryFields(item.cat)}>Load fields for selected category</button>
                </div>
                {#if categoryNotice}<p class="help category-message">{categoryNotice}</p>{/if}
                {#if categorySuggestions.length}
                    <div class="category-suggestions" aria-label="eBay category suggestions">
                        {#each categorySuggestions as suggestion}
                            <button type="button" on:click={() => chooseCategory(suggestion)}><strong>{suggestion.categoryName}</strong><span>{suggestion.path} · ID {suggestion.categoryId}</span></button>
                        {/each}
                    </div>
                {/if}
            </div>
            <label class="field wide"><span>Condition Note</span><input bind:value={item.cnote} /></label>
            <label class="field"><span>Brand</span><input bind:value={item.brand} /></label>
            <label class="field"><span>Model</span><input bind:value={item.model} /></label>
            <label class="field"><span>Size</span><input bind:value={item.size} /></label>
            <label class="field"><span>Color</span><input bind:value={item.color} /></label>
            <label class="field"><span>Department</span><input bind:value={item.dept} /></label>
            <label class="field"><span>Type</span><input bind:value={item.type} on:change={() => item = applyClientRules(item)} /></label>
            <label class="field"><span>Style</span><input bind:value={item.style} /></label>
            <label class="field"><span>Theme</span><input bind:value={item.theme} /></label>
            <label class="field"><span>Material</span><input bind:value={item.mat} /></label>
            <label class="field"><span>Pattern</span><input bind:value={item.pat} /></label>
            <label class="field"><span>Sleeve Length</span><input bind:value={item.slv} /></label>
            <label class="field"><span>Neckline</span><input bind:value={item.nk} /></label>
            <label class="field"><span>Season</span><input bind:value={item.sea} /></label>
            <label class="field"><span>Occasion</span><input bind:value={item.occ} /></label>
            <label class="field"><span>Size Type</span><input bind:value={item.st} /></label>
            <label class="field"><span>Vintage</span><select bind:value={item.vin} on:change={() => item = applyClientRules(item)}><option value="No">No</option><option value="Yes (pre-1999)">Yes (pre-1999)</option></select></label>
            <label class="field"><span>Made In label</span><input bind:value={item.madeIn} /></label>
            <label class="field"><span>Interior patch / serial</span><input bind:value={item.serialNumber} /></label>
            <label class="field wide"><span>Measurements</span><input bind:value={item.measurements} /></label>
            <label class="field wide"><span>PicURL</span><input bind:value={item.pic} placeholder="[SELLER TO ADD IMAGE URLS]" /></label>
            <label class="field wide"><span>Description HTML</span><textarea bind:value={item.desc} rows="8"></textarea></label>
            <label class="field wide"><span>Seller notes</span><textarea bind:value={item.notes} rows="4"></textarea></label>
            {#if categoryFields.length}
                <section class="wide dynamic-aspects" aria-label="Current eBay category-specific fields">
                    <h3>Current eBay fields for {item.categoryName || `category ${item.cat}`}</h3>
                    <p>Fields marked <b>Required</b> come directly from eBay’s Taxonomy API. Enter only facts you can confirm.</p>
                    <div class="form aspect-grid">
                        {#each categoryFields as field}
                            <label class="field">
                                <span>{field.name} {#if field.required}<em class="required">Required</em>{:else if field.recommended}<em>Recommended</em>{/if}</span>
                                {#if field.values?.length && field.values.length <= 40}
                                    <select value={categoryFieldValue(field.name)} on:change={(event) => updateCategoryField(field.name, event.currentTarget.value)}>
                                        <option value="">Select or leave blank</option>
                                        {#each field.values as value}<option value={value}>{value}</option>{/each}
                                    </select>
                                {:else}
                                    <input value={categoryFieldValue(field.name)} on:input={(event) => updateCategoryField(field.name, event.currentTarget.value)} placeholder={field.multiSelect ? "Use a seller-confirmed value" : "Enter a seller-confirmed value"} />
                                {/if}
                            </label>
                        {/each}
                    </div>
                </section>
            {:else if item.cat}
                <p class="help wide">Choose “Find eBay categories” or reselect the category to load eBay’s live required item specifics.</p>
            {/if}
            <div class="actions wide">
                <button type="button" on:click={() => item.desc = description(item)}>Generate description</button>
                <button class="primary" type="button" on:click={addToQueue}>Add reviewed item to queue</button>
            </div>
        </section>
    {/if}

    {#if tab === "queue"}
        <section class="panel">
            <div class="stats">
                <div><strong>{queue.length}</strong><span>Items</span></div>
                <div><strong>${queueTotal.toFixed(2)}</strong><span>Total</span></div>
                <div><strong>${queueAverage.toFixed(2)}</strong><span>Average</span></div>
            </div>
            {#if !queue.length}
                <p class="empty">Analyze an item, review the fields, then add it here.</p>
            {:else}
                {#each queue as queued, index}
                    <div class="queue-row">
                        <div><strong>{queued.title}</strong><span>{queued.brand} / {queued.size} / ${Number(queued.price || 0).toFixed(2)}{queued.ebayFeedTaskId ? ` / feed task ${queued.ebayFeedTaskId}` : ""}</span></div>
                        <button type="button" on:click={() => editQueued(index)}>Edit</button>
                        <button type="button" on:click={() => queue = queue.filter((_, i) => i !== index)}>Remove</button>
                    </div>
                {/each}
                <div class="actions">
                    <button class="primary" disabled={draftLoading} on:click={sendDraftQueue}>{draftLoading ? "Sending..." : "Send Seller Hub Drafts to eBay"}</button>
                    <button on:click={exportDraftQueue}>Download Seller Hub Draft CSV</button>
                    <button on:click={exportQueue}>Download legacy File Exchange CSV</button>
                    <button on:click={backupQueue}>Download JSON backup</button>
                    <button on:click={() => queue = []}>Clear queue</button>
                </div>
            {/if}
            <input bind:this={restoreInput} class="hidden" type="file" accept="application/json" on:change={restoreBackup} />
            <button type="button" on:click={() => restoreInput.click()}>Restore JSON backup</button>
            <div class="notice warn">
                <strong>Seller Hub draft workflow</strong>
                <p><b>Send Seller Hub Drafts to eBay</b> uploads this queue through eBay’s Sell Feed API as a Seller Hub draft feed. It is intended for Seller Hub draft processing, not direct live publishing.</p>
                <p><b>Download Seller Hub Draft CSV</b> remains available if you want to inspect the file or upload manually in <b>Seller Hub → Reports → Uploads → Create new drafts</b>.</p>
                <p><b>Legacy File Exchange CSV</b> is for a matching legacy/File Exchange template only; do not upload it as a Seller Hub Draft template. Local phone photos must still be added in eBay or hosted at public URLs.</p>
            </div>
        </section>
    {/if}

    {#if tab === "settings"}
        <section class="panel form">
            <div class="wide notice info">
                <strong>eBay connection</strong>
                <p>Reconnect when eBay permissions change. After approving access, copy the returned <code>EBAY_REFRESH_TOKEN</code> into your host config and restart the app.</p>
                {#if oauthStatus}<p>{oauthStatus}</p>{/if}
                <div class="actions">
                    <button type="button" disabled={oauthLoading} on:click={reconnectEbay}>{oauthLoading ? "Opening..." : "Reconnect eBay"}</button>
                    <button type="button" disabled={oauthLoading} on:click={checkEbayConnection}>Check connection</button>
                </div>
            </div>
            <label class="field"><span>Location</span><input bind:value={seller.location} /></label>
            <label class="field"><span>Postal Code</span><input bind:value={seller.postalCode} /></label>
            <label class="field"><span>Country Code</span><input bind:value={seller.countryCode} /></label>
            <label class="field wide"><span>Payment Profile</span><input bind:value={seller.paymentProfileName} /></label>
            <label class="field wide"><span>Shipping Profile</span><input bind:value={seller.shippingProfileName} /></label>
            <label class="field wide"><span>Return Profile</span><input bind:value={seller.returnProfileName} /></label>
            <label class="field"><span>Dispatch Days</span><input bind:value={seller.dispatchTimeMax} inputmode="numeric" /></label>
            <p class="help wide">Hosted analysis uses Heroku Config Vars only: GROQ_API_KEY, OPENROUTER_API_KEY, or NVIDIA_NIM_API_KEY. Never paste keys into this page.</p>
        </section>
    {/if}
</div>
