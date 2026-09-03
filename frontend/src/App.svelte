<script>
    import "./app.css";
    import { analyzeImages, downloadCSV, downloadJSON } from "$lib/api";

    const emptyItem = {
        title: "", price: "", cid: "3000", cnote: "", cat: "", brand: "",
        size: "", color: "", dept: "", type: "", style: "", mat: "", pat: "",
        slv: "", nk: "", sea: "All Seasons", occ: "Casual", st: "Regular",
        vin: "No", desc: "", notes: "", madeIn: "", serialNumber: "",
        measurements: "", pic: ""
    };
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
    let restoreInput;
    let localPipeline = null;

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
    }

    function removeFile(index) {
        const next = files.slice();
        next.splice(index, 1);
        setFiles(next);
    }

    async function analyze() {
        error = "";
        if (!files.length) {
            error = "Upload at least one item photo first.";
            return;
        }
        loading = true;
        try {
            status = engine === "hosted" ? "Compressing and uploading photos..." : "Starting browser-local model...";
            const hostedFiles = engine === "hosted" ? await compactHostedFiles(files) : files;
            status = engine === "hosted" ? "Uploading compressed photos for secure analysis..." : status;
            const result = engine === "hosted" ? await analyzeImages(hostedFiles, seller) : await localAnalyze();
            item = normalizeForForm(result);
            status = result.demo ? "Demo result loaded. Review required." : `Analysis complete via ${result.provider || engine}. Review required.`;
            tab = "edit";
        } catch (err) {
            error = friendlyAnalyzeError(err);
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
        const geminiHint = failures.some((failure) => failure.provider === "gemini")
            ? " If Gemini is out of credits, remove GEMINI_API_KEY from Heroku Config Vars."
            : "";
        return `${err.message || "Analysis failed."} ${lines.join("; ")}. No demo data was shown.${geminiHint}`;
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
        const next = { ...nextItem };
        if (isBag(next)) {
            next.slv = "N/A - bag";
            next.nk = "N/A - bag";
            next.size = "N/A - bag";
            next.st = "N/A - bag";
        } else if (isShoe(next)) {
            next.slv = "N/A - footwear";
            next.nk = "N/A - footwear";
        }
        if (next.vin !== "Yes (pre-1999)") next.vin = "No";
        if (next.vin === "Yes (pre-1999)" && !/vintage/i.test(next.title || "")) {
            next.title = `Vintage ${next.title || ""}`.trim().slice(0, 80);
        }
        next.title = String(next.title || "").slice(0, 80);
        return next;
    }

    function addToQueue() {
        let reviewed = applyClientRules(item);
        if (!reviewed.desc) reviewed = { ...reviewed, desc: description(reviewed) };
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
        try {
            await downloadCSV(queue, seller);
        } catch (err) {
            error = err.message || String(err);
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

    function isBag(source) {
        return ["169291", "169284"].includes(String(source.cat || "")) || /handbag|crossbody|clutch|backpack|tote|purse/i.test(source.type || "");
    }

    function isShoe(source) {
        return String(source.cat || "") === "93427" || /shoe|sneaker|boot|loafer|sandal/i.test(source.type || "");
    }
</script>

<div class="shell">
    <header class="topbar">
        <div>
            <h1>HHT eBay Listing Builder</h1>
            <p>Photo analysis, seller review, queue, and fixed-price CSV export</p>
        </div>
        <strong>{queue.length} item{queue.length === 1 ? "" : "s"}</strong>
    </header>

    <nav class="tabs" aria-label="Main navigation">
        <button class:on={tab === "analyze"} on:click={() => tab = "analyze"}>Analyze</button>
        <button class:on={tab === "edit"} on:click={() => tab = "edit"}>Edit</button>
        <button class:on={tab === "queue"} on:click={() => tab = "queue"}>Queue</button>
        <button class:on={tab === "settings"} on:click={() => tab = "settings"}>Settings</button>
    </nav>

    {#if error}<div class="notice error">{error}</div>{/if}
    {#if status}<div class="notice info">{status}</div>{/if}

    {#if tab === "analyze"}
        <section class="panel">
            <label class="field">
                <span>Analysis engine</span>
                <select bind:value={engine}>
                    <option value="hosted">Hosted secure analysis</option>
                    <option value="local">Browser-local SmolVLM experimental</option>
                </select>
            </label>
            <p class="help">
                {engine === "hosted"
                    ? "Photos go to this Heroku app, which calls server-side provider keys only."
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
            <label class="field"><span>Condition</span><select bind:value={item.cid}><option value="1000">1000 New with Tags</option><option value="1500">1500 New without Tags</option><option value="3000">3000 Pre-Owned</option><option value="4000">4000 Very Good</option><option value="5000">5000 Good</option><option value="6000">6000 Acceptable</option></select></label>
            <label class="field wide"><span>Category</span><select bind:value={item.cat}><option value="">Needs seller review</option><option value="15724">Women's Tops / Blouses</option><option value="63861">Women's Dresses</option><option value="63867">Women's Jeans / Pants</option><option value="11484">Women's Sweaters / Cardigans</option><option value="57988">Jackets / Coats</option><option value="63866">Women's Skirts</option><option value="185100">Women's Activewear Pants / Leggings</option><option value="15687">Men's T-Shirts</option><option value="11483">Men's Jeans</option><option value="57990">Men's Casual Shirts / Polos</option><option value="155183">Men's Sweatshirts / Hoodies</option><option value="93427">Men's Casual Shoes / Boat Shoes</option><option value="169291">Handbags / Clutches / Crossbodies</option><option value="169284">Backpacks</option></select></label>
            <label class="field wide"><span>Condition Note</span><input bind:value={item.cnote} /></label>
            <label class="field"><span>Brand</span><input bind:value={item.brand} /></label>
            <label class="field"><span>Size</span><input bind:value={item.size} /></label>
            <label class="field"><span>Color</span><input bind:value={item.color} /></label>
            <label class="field"><span>Department</span><input bind:value={item.dept} /></label>
            <label class="field"><span>Type</span><input bind:value={item.type} on:change={() => item = applyClientRules(item)} /></label>
            <label class="field"><span>Style</span><input bind:value={item.style} /></label>
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
                        <div><strong>{queued.title}</strong><span>{queued.brand} / {queued.size} / ${Number(queued.price || 0).toFixed(2)}</span></div>
                        <button type="button" on:click={() => editQueued(index)}>Edit</button>
                        <button type="button" on:click={() => queue = queue.filter((_, i) => i !== index)}>Remove</button>
                    </div>
                {/each}
                <div class="actions">
                    <button class="primary" on:click={exportQueue}>Download eBay CSV</button>
                    <button on:click={backupQueue}>Download JSON backup</button>
                    <button on:click={() => queue = []}>Clear queue</button>
                </div>
            {/if}
            <input bind:this={restoreInput} class="hidden" type="file" accept="application/json" on:change={restoreBackup} />
            <button type="button" on:click={() => restoreInput.click()}>Restore JSON backup</button>
            <p class="help">CSV export uses a PicURL placeholder unless you enter public image URLs. Local phone photos are not attached by CSV alone.</p>
        </section>
    {/if}

    {#if tab === "settings"}
        <section class="panel form">
            <label class="field"><span>Location</span><input bind:value={seller.location} /></label>
            <label class="field"><span>Postal Code</span><input bind:value={seller.postalCode} /></label>
            <label class="field"><span>Country Code</span><input bind:value={seller.countryCode} /></label>
            <label class="field wide"><span>Payment Profile</span><input bind:value={seller.paymentProfileName} /></label>
            <label class="field wide"><span>Shipping Profile</span><input bind:value={seller.shippingProfileName} /></label>
            <label class="field wide"><span>Return Profile</span><input bind:value={seller.returnProfileName} /></label>
            <label class="field"><span>Dispatch Days</span><input bind:value={seller.dispatchTimeMax} inputmode="numeric" /></label>
            <p class="help wide">Hosted analysis uses Heroku Config Vars only: OPENROUTER_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY. Never paste keys into this page.</p>
        </section>
    {/if}
</div>
