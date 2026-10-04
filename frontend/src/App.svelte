<script>
    import { onMount } from "svelte";
    import "./app.css";
    import { analyzeImages, commerceApproveRotation, commerceJob, commercePerformance, commerceStartFulfillmentSync, commerceStartPerformanceSync, downloadCSV, downloadDraftCSV, downloadJSON, ebayCategoryAspects, ebayCategorySuggestions, ebayFeedTask, ebayOAuthStart, ebayOAuthStatus, photoStorageStatus, sendDraftFeed, startNvidiaAnalysis, uploadListingPhotos } from "$lib/api";
    import { applyClientItemRules, CATEGORY_OPTIONS, EMPTY_ITEM } from "$lib/ebay";
    import CommerceAgent from "$lib/components/CommerceAgent.svelte";
    import AuthGate from "$lib/components/AuthGate.svelte";
    import { supabase } from "$lib/supabase";

    const emptyItem = EMPTY_ITEM;
    const breakGroundEnabled = String(import.meta.env.VITE_BREAKGROUND_ENABLED || "false").toLowerCase() === "true";
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
    let stagingPhotos = [];
    let selectedPhotoIds = [];
    let dragActive = false;
    let stagingInput;
    let item = load("hht_current_item", emptyItem);
    let queue = load("hht_queue", []);
    let analysisJobs = load("hht_analysis_jobs", []);
    const analysisSourceFiles = new Map();
    let seller = load("hht_seller_defaults", defaultSeller);
    let analysisHints = load("hht_analysis_hints", { brand: "", model: "", itemType: "", category: "", searchTerms: "" });
    let autoDraftEnabled = loadFlag("hht_auto_draft_enabled");
    let status = "";
    let error = "";
    let loading = false;
    let canTryAlternate = false;
    let alternateProvider = "";
    let draftLoading = false;
    let autoDraftInFlight = false;
    let restoreInput;
    let localPipeline = null;
    let categoryQuery = "";
    let categorySuggestions = [];
    let categoryFields = [];
    let categoryLoading = false;
    let categoryNotice = "";
    let oauthLoading = false;
    let oauthStatus = "";
    let performanceData = { capacity: {}, rotation: [], records: [] };
    let performanceLoading = false;
    let performanceSyncing = false;
    let rotationSelected = [];
    let photoStorage = { provider: "disabled", configured: false };
    let photoStorageRequest = 0;
    let onboardingDismissed = loadFlag("hht_onboarding_dismissed");

    const canonicalAspectKeys = {
        brand: "brand", model: "model", size: "size", color: "color", department: "dept",
        type: "type", style: "style", theme: "theme", material: "mat", pattern: "pat",
        "sleeve length": "slv", neckline: "nk", season: "sea", occasion: "occ",
        "size type": "st", vintage: "vin", "made in": "madeIn", "country/region of manufacture": "madeIn",
        "serial number": "serialNumber", measurements: "measurements", condition: "cnote"
    };
    const canonicalEbayFields = [
        ["Brand", "brand"], ["Model", "model"], ["Size", "size"], ["Color", "color"],
        ["Department", "dept"], ["Type", "type"], ["Style", "style"], ["Theme", "theme"],
        ["Material", "mat"], ["Pattern", "pat"], ["Sleeve Length", "slv"], ["Neckline", "nk"],
        ["Season", "sea"], ["Occasion", "occ"], ["Size Type", "st"], ["Vintage", "vin"],
        ["Made In", "madeIn"], ["Serial Number", "serialNumber"], ["Measurements", "measurements"]
    ].map(([name, key]) => ({ name, key, required: false, recommended: false, values: [], multiSelect: false }));

    $: titleLength = (item.title || "").length;
    $: queueTotal = queue.reduce((sum, next) => sum + (Number.parseFloat(next.price) || 0), 0);
    $: queueAverage = queue.length ? queueTotal / queue.length : 0;
    $: analysisTimingSamples = analysisJobs.map((job) => job.result?.analysisTimings).filter((timing) => timing && timing.firstPassSeconds !== undefined && timing.totalSeconds !== undefined && !timing.cacheHit);
    $: analysisMedianFirstPass = median(analysisTimingSamples.map((timing) => Number(timing.firstPassSeconds)));
    $: analysisMedianTotal = median(analysisTimingSamples.map((timing) => Number(timing.totalSeconds)));
    $: persist("hht_queue", queue);
    $: persist("hht_analysis_jobs", analysisJobs);
    $: persist("hht_seller_defaults", seller);
    $: persist("hht_analysis_hints", analysisHints);
    $: persist("hht_auto_draft_enabled", autoDraftEnabled);
    $: persist("hht_current_item", item);
    $: reviewNotes = sellerReviewNotes(item);
    $: itemReviewFindings = getReviewFindings(item);
    $: visibleCategoryFields = categoryFields.filter((field) => !canonicalAspectKeys[aspectKey(field.name)]);
    $: ebaySpecificFields = [
        ...canonicalEbayFields.map((base) => categoryFields.find((field) => aspectKey(field.name) === aspectKey(base.name)) || base),
        ...visibleCategoryFields,
    ];
    $: selectedStagingPhotos = stagingPhotos.filter((photo) => selectedPhotoIds.includes(photo.id) && !photo.processed);

    onMount(() => {
        for (const job of analysisJobs.filter((entry) => ["queued", "running", "auditing"].includes(entry.status))) {
            void monitorListingAnalysis(job.jobId, []);
        }
        if (supabase) {
            const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
                setTimeout(() => refreshPhotoStorage(session), 0);
            });
            return () => subscription.unsubscribe();
        }
    });

    async function refreshPhotoStorage(sessionOrEvent) {
        const request = ++photoStorageRequest;
        const session = sessionOrEvent?.detail ?? sessionOrEvent;
        if (!session) {
            photoStorage = { provider: "disabled", configured: false };
            return;
        }
        try {
            const result = await photoStorageStatus();
            if (request === photoStorageRequest) photoStorage = result || photoStorage;
        } catch {
            if (request === photoStorageRequest) photoStorage = { provider: "disabled", configured: false };
        }
    }

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

    function loadFlag(key) {
        try {
            return JSON.parse(localStorage.getItem(key) || "false") === true;
        } catch {
            return false;
        }
    }

    function median(values) {
        const sorted = values.filter(Number.isFinite).sort((left, right) => left - right);
        if (!sorted.length) return null;
        const middle = Math.floor(sorted.length / 2);
        return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }

    function dismissOnboarding() {
        onboardingDismissed = true;
        persist("hht_onboarding_dismissed", true);
    }

    async function onFilesSelected(event) {
        await addStagingPhotos(Array.from(event.target.files || []));
        if (event.target) event.target.value = "";
    }

    async function onDrop(event) {
        event.preventDefault();
        dragActive = false;
        await addStagingPhotos(Array.from(event.dataTransfer?.files || []));
    }

    async function addStagingPhotos(incoming) {
        const images = incoming.filter((file) => file.type.startsWith("image/"));
        if (!images.length) {
            error = "Choose image files to add to the staging grid.";
            return;
        }
        const existing = new Set(stagingPhotos.map((photo) => `${photo.name}:${photo.size}:${photo.lastModified}`));
        const additions = images
            .filter((file) => !existing.has(`${file.name}:${file.size}:${file.lastModified}`))
            .map((file) => ({
                id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID?.() || Date.now()}`,
                file,
                name: file.name,
                size: file.size,
                lastModified: file.lastModified,
                url: URL.createObjectURL(file),
                processed: false,
            }));
        stagingPhotos = [...stagingPhotos, ...additions];
        error = "";
        status = `${stagingPhotos.length} photo${stagingPhotos.length === 1 ? "" : "s"} staged. Select the front, back, tag, and detail photos for one item.`;
    }

    function toggleStagingPhoto(photo) {
        if (photo.processed || loading) return;
        if (selectedPhotoIds.includes(photo.id)) {
            selectedPhotoIds = selectedPhotoIds.filter((id) => id !== photo.id);
            return;
        }
        if (selectedPhotoIds.length >= 3) {
            error = "Select up to 3 photos per item for the current Groq analysis endpoint.";
            return;
        }
        selectedPhotoIds = [...selectedPhotoIds, photo.id];
        error = "";
    }

    function removeStagingPhoto(photo) {
        if (photo.processed) return;
        URL.revokeObjectURL(photo.url);
        stagingPhotos = stagingPhotos.filter((entry) => entry.id !== photo.id);
        selectedPhotoIds = selectedPhotoIds.filter((id) => id !== photo.id);
    }

    function clearStagingPhotos() {
        stagingPhotos.filter((photo) => !photo.processed).forEach((photo) => URL.revokeObjectURL(photo.url));
        stagingPhotos = stagingPhotos.filter((photo) => photo.processed);
        selectedPhotoIds = [];
        status = stagingPhotos.length ? `${stagingPhotos.length} processed photo${stagingPhotos.length === 1 ? "" : "s"} locked in staging.` : "Staging grid cleared.";
    }

    async function processStagedItem() {
        const group = selectedStagingPhotos.map((photo) => photo.file);
        if (!group.length) {
            error = "Select one to three unprocessed photos for one item first.";
            return;
        }
        files = group;
        previews = group.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
        await analyze({ sourceFiles: group });
        if (!error) {
            const processedIds = new Set(selectedStagingPhotos.map((photo) => photo.id));
            stagingPhotos = stagingPhotos.map((photo) => processedIds.has(photo.id) ? { ...photo, processed: true } : photo);
            selectedPhotoIds = [];
            status = "Item processed. Its photos are locked; select the next unprocessed group.";
        }
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
        const analysisFiles = options.sourceFiles || files;
        if (!analysisFiles.length) {
            error = "Upload at least one item photo first.";
            return;
        }
        loading = true;
        try {
            const usesHostedUpload = engine === "hosted";
            status = usesHostedUpload ? "Compressing and uploading photos..." : "Starting browser-local model...";
            const hostedFiles = usesHostedUpload ? await compactHostedFiles(analysisFiles) : analysisFiles;
            status = usesHostedUpload ? "Uploading compressed photos for secure analysis..." : status;
            if (engine === "hosted") {
                const started = await analyzeImages(hostedFiles, seller, { ...options, analysisHints });
                const analysisJob = {
                    jobId: started.jobId,
                    status: started.status || "queued",
                    progress: 0,
                    label: analysisFiles.map((file) => file.name).join(", "),
                    result: null,
                    startedAt: new Date().toISOString(),
                };
                analysisSourceFiles.set(started.jobId, analysisFiles);
                analysisJobs = [analysisJob, ...analysisJobs.filter((job) => job.jobId !== started.jobId)].slice(0, 30);
                status = "Analysis queued. You can continue grouping photos while each item processes.";
                void monitorListingAnalysis(started.jobId, analysisFiles);
                return;
            }
            let result;
            result = await localAnalyze();
            result = { ...result, photoCount: analysisFiles.length };
            let storageNotice = "";
            if (photoStorage.configured && hostedFiles.length) {
                try {
                    const stored = await uploadListingPhotos(analysisFiles, item.sku || "unassigned");
                    const ebayUrls = (stored.assets || []).map((asset) => asset.ebayUrl).filter(Boolean);
                    if (ebayUrls.length) result = { ...result, pic: ebayUrls.join(" "), photoAssets: stored.assets };
                    storageNotice = ` ${ebayUrls.length} eBay-ready photo URL${ebayUrls.length === 1 ? "" : "s"} saved.${stored.failures?.length ? ` ${stored.failures.length} photo${stored.failures.length === 1 ? "" : "s"} could not be saved.` : ""}`;
                } catch (storageError) {
                    storageNotice = " Persistent photo storage was unavailable, so photos remain analysis-only.";
                }
            }
            item = normalizeForForm(result);
            if (item.cat) void loadCategoryFields(item.cat);
            status = result.demo ? "Demo result loaded. Review required." : `${options.isRerun ? "Re-analysis" : "Analysis"} complete via ${result.provider || engine}.${storageNotice} Review required.`;
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

    async function monitorListingAnalysis(jobId, sourceFiles = []) {
        const expiresAt = Date.now() + 120000;
        let storedPhotos = false;
        while (Date.now() < expiresAt) {
            try {
                const job = await listingAnalysisJob(jobId);
                const previous = analysisJobs.find((entry) => entry.jobId === jobId)?.result || {};
                let updated = job.result?.listing || previous;
                if (previous.pic && !updated.pic) updated = { ...updated, pic: previous.pic, photoAssets: previous.photoAssets, photoStorageStatus: previous.photoStorageStatus, photoStorageRequired: previous.photoStorageRequired };
                if (updated && !storedPhotos && sourceFiles.length && photoStorage.configured) {
                    storedPhotos = true;
                    updated = { ...updated, photoStorageStatus: "pending", photoStorageRequired: true };
                    void persistAnalysisPhotos(jobId, sourceFiles);
                }
                const withJobId = updated ? { ...updated, analysisJobId: jobId } : updated;
                analysisJobs = analysisJobs.map((entry) => entry.jobId === jobId ? {
                    ...entry,
                    status: job.status === "completed" ? "complete" : job.status === "failed" ? "failed" : updated ? "auditing" : job.status,
                    progress: job.progress || entry.progress,
                    result: withJobId || entry.result,
                    error: job.error || "",
                } : entry);
                if (item.analysisJobId === jobId && withJobId) item = normalizeForForm(withJobId);
                if (job.status === "failed" || job.status === "completed") return;
            } catch {
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 1500));
        }
    }

    async function persistAnalysisPhotos(jobId, sourceFiles) {
        try {
            const stored = await uploadListingPhotos(sourceFiles, "unassigned");
            const urls = (stored.assets || []).map((asset) => asset.ebayUrl).filter((url) => url.startsWith("https://"));
            analysisJobs = analysisJobs.map((entry) => entry.jobId === jobId && entry.result
                ? { ...entry, result: { ...entry.result, pic: urls.join(" "), photoAssets: stored.assets, photoStorageStatus: urls.length ? "stored" : "unavailable" } }
                : entry);
            if (item.analysisJobId === jobId && urls.length) item = { ...item, pic: urls.join(" "), photoAssets: stored.assets, photoStorageStatus: "stored" };
        } catch {
            analysisJobs = analysisJobs.map((entry) => entry.jobId === jobId && entry.result
                ? { ...entry, result: { ...entry.result, photoStorageStatus: "unavailable" } }
                : entry);
            if (item.analysisJobId === jobId) item = { ...item, photoStorageStatus: "unavailable" };
        }
    }

    function openAnalysisResult(job) {
        if (!job?.result) return;
        item = normalizeForForm(job.result);
        previews.forEach((preview) => URL.revokeObjectURL(preview.url));
        files = analysisSourceFiles.get(job.jobId) || [];
        previews = files.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
        if (item.cat) void loadCategoryFields(item.cat);
        tab = "edit";
    }

    async function localAnalyze() {
        status = "Loading SmolVLM in this browser. This may be slow or unsupported on some phones.";
        const mod = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1");
        if (!localPipeline) {
            localPipeline = await mod.pipeline("image-text-to-text", "HuggingFaceTB/SmolVLM-256M-Instruct", { device: "webgpu", dtype: "q4" });
        }
        const images = await Promise.all(files.map(fileToDataUrl));
        const content = images.map((url) => ({ type: "image", url }));
        const hintText = Object.entries(analysisHints).filter(([, value]) => String(value || "").trim()).map(([key, value]) => `${key}: ${value}`).join("; ");
        content.push({ type: "text", text: `Inspect every clothing, shoe, or bag photo and return JSON keys title, price, cid, cnote, cat, brand, size, color, dept, type, style, mat, pat, slv, nk, sea, occ, st, vin, desc, notes, madeIn, serialNumber, measurements. Use Not visible rather than guessing. Seller clues are hypotheses to verify, not facts: ${hintText || "none"}.` });
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
        for (const file of sourceFiles.slice(0, 5)) {
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
                    const max = 1280;
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
        categoryFields = [];
        try {
            const result = await ebayCategoryAspects(id);
            if (item.cat === id) {
                categoryFields = result.fields || [];
                item = { ...item, categoryRequiredAspects: result.requiredAspects || [] };
                categoryNotice = result.message || "";
            }
        } catch (err) {
            categoryFields = [];
            if (item.cat === id) {
                item = { ...item, categoryRequiredAspects: [] };
                categoryNotice = err.message || "eBay category fields are unavailable.";
            }
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

    function categoryFieldValue(name) {
        const key = canonicalAspectKeys[aspectKey(name)];
        return key ? item[key] || "" : item.itemSpecifics?.[name] || "";
    }

    function categoryFieldValues(name) {
        return String(categoryFieldValue(name) || "")
            .split("|")
            .map((value) => value.trim())
            .filter(Boolean);
    }

    function aspectKey(name) {
        return String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
    }

    function updateCategoryField(name, value) {
        const key = canonicalAspectKeys[aspectKey(name)];
        if (key) {
            item = { ...item, [key]: value };
            return;
        }
        item = { ...item, itemSpecifics: { ...(item.itemSpecifics || {}), [name]: value } };
    }

    function toggleCategoryFieldValue(name, value, checked) {
        const next = new Set(categoryFieldValues(name));
        if (checked) next.add(value);
        else next.delete(value);
        updateCategoryField(name, Array.from(next).join(" | "));
    }

    function reviewedCandidate(source = item) {
        let reviewed = applyClientRules(source);
        if (!reviewed.desc) reviewed = { ...reviewed, desc: description(reviewed) };
        return reviewed;
    }

    function approvedDraftQueue() {
        return queue.filter((entry) => entry.approved === true && !entry.ebayFeedTaskId);
    }

    function firstInvalidQueuedItem(source = queue) {
        for (let index = 0; index < source.length; index += 1) {
            const reviewed = reviewedCandidate(source[index]);
            if (reviewed.reviewAcknowledged !== true) {
                return { index, reviewed, message: "Open this item in Review and acknowledge its Needs review list before sending it to eBay." };
            }
            const validation = validateItem(reviewed);
            if (validation) return { index, reviewed, message: validation };
        }
        return null;
    }

    function queueIdentity(candidate) {
        for (const field of ["listingId", "offerId", "sku", "customLabel", "ebayUrl"]) {
            const value = String(candidate?.[field] || "").trim().toLowerCase();
            if (value) return `${field}:${value}`;
        }
        return `content:${[candidate?.title, candidate?.cat, candidate?.price, candidate?.pic]
            .map((value) => String(value || "").trim().toLowerCase())
            .join("|")}`;
    }

    function addToQueue() {
        const reviewed = reviewedCandidate(item);
        const validation = validateItem(reviewed);
        if (validation) {
            error = validation;
            tab = "edit";
            return;
        }
        if (queue.some((entry) => queueIdentity(entry) === queueIdentity(reviewed))) {
            error = "This listing is already in the queue. Edit the existing row instead of adding a duplicate.";
            tab = "queue";
            return;
        }
        queue = [...queue, { ...reviewed, reviewAcknowledged: true, approved: true, approvedAt: new Date().toISOString() }];
        item = { ...emptyItem };
        status = "Item added to queue.";
        tab = "queue";
        scheduleAutoDraftUpload();
    }

    function validateItem(candidate) {
        if (candidate.photoStorageRequired && candidate.photoStorageStatus === "pending") return "Wait for signed photo URLs to finish before queueing this hosted listing.";
        if (candidate.photoStorageRequired && !String(candidate.pic || "").split(/[\s,]+/).some((url) => url.startsWith("https://"))) return "This hosted listing needs a signed HTTPS photo URL before it can be queued.";
        if (!candidate.title || candidate.title.length > 80) return "Title is required and must be 80 characters or fewer.";
        if (!(Number.parseFloat(candidate.price) > 0)) return "Enter a positive fixed price.";
        if (!(Number(candidate.photoCount) > 0 || String(candidate.pic || "").trim())) return "Attach at least one item photo before queueing.";
        if (!candidate.cat) return "Choose a supplied eBay category before queueing.";
        if (!candidate.brand) return "Brand is required. Use Not visible or No Brand if needed.";
        if (candidate.brand !== "Not visible" && !String(candidate.title || "").toLowerCase().includes(String(candidate.brand).toLowerCase())) return "Include the selected brand in the listing title or correct the brand.";
        const inconsistentSpecific = specificConsistencyFindings(candidate)[0];
        if (inconsistentSpecific) return inconsistentSpecific;
        if (["3000", "4000", "5000", "6000"].includes(String(candidate.cid)) && !candidate.cnote) return "Add a condition note for used condition codes.";
        const required = candidate.categoryRequiredAspects || [];
        const missing = required.find((name) => {
            const key = canonicalAspectKeys[aspectKey(name)];
            const value = key ? candidate[key] : candidate.itemSpecifics?.[name];
            return !String(value || "").trim() || /^not visible$/i.test(String(value || "").trim());
        });
        if (missing) return `Complete the Taxonomy-required ${missing} specific before queueing.`;
        return "";
    }

    function getReviewFindings(candidate) {
        const findings = [...(candidate.needsReview || []).map((entry) => entry.reason || `${entry.field}: review needed` )];
        if (candidate.auditStatus === "running") findings.push("NVIDIA's second-pass audit is still running; it may add findings after you queue this draft.");
        if (!candidate.title || candidate.title.length > 80) findings.push("Title must contain 1 to 80 characters.");
        if (!(Number.parseFloat(candidate.price) > 0)) findings.push("Price must be greater than zero.");
        if (!(Number(candidate.photoCount) > 0 || String(candidate.pic || "").trim())) findings.push("No item photo is attached to this listing.");
        if (candidate.photoStorageRequired && candidate.photoStorageStatus === "pending") findings.push("Signed photo URL generation is still running.");
        if (candidate.photoStorageRequired && !String(candidate.pic || "").split(/[\s,]+/).some((url) => url.startsWith("https://"))) findings.push("A signed HTTPS photo URL is required before sending this hosted listing.");
        if (candidate.brand && candidate.brand !== "Not visible" && !String(candidate.title || "").toLowerCase().includes(String(candidate.brand).toLowerCase())) {
            findings.push("Brand is not present in the title.");
        }
        findings.push(...specificConsistencyFindings(candidate));
        if (["3000", "5000", "6000"].includes(String(candidate.cid)) && !String(candidate.cnote || "").trim()) {
            findings.push("Add a condition description for this used item.");
        }
        const required = candidate.categoryRequiredAspects || [];
        for (const name of required) {
            const key = canonicalAspectKeys[aspectKey(name)];
            const value = key ? candidate[key] : candidate.itemSpecifics?.[name];
            if (!String(value || "").trim() || /^not visible$/i.test(String(value || "").trim())) findings.push(`Missing required eBay specific: ${name}.`);
        }
        if (candidate.cat && candidate.categoryRequiredAspects === undefined) findings.push("eBay Taxonomy-required specifics have not been checked.");
        if (candidate.rulesCheck?.findings?.length) findings.push(...candidate.rulesCheck.findings.map((entry) => entry.message));
        return [...new Set(findings)];
    }

    function specificConsistencyFindings(candidate) {
        const fields = {
            Brand: "brand", Size: "size", Color: "color", Department: "dept", Type: "type",
            Style: "style", Material: "mat", Pattern: "pat", Vintage: "vin", "Made In": "madeIn",
        };
        const specifics = candidate.itemSpecifics || {};
        return Object.entries(fields).flatMap(([label, key]) => {
            const name = Object.keys(specifics).find((entry) => entry.toLowerCase() === label.toLowerCase());
            if (!name) return [];
            const specificValues = Array.isArray(specifics[name]) ? specifics[name] : [specifics[name]];
            const canonical = String(candidate[key] || "").trim();
            if (!canonical || /^not visible$/i.test(canonical) || specificValues.some((value) => String(value).trim().toLowerCase() === canonical.toLowerCase())) return [];
            return [`${label} conflicts with the listing field.`];
        });
    }

    function editQueued(index) {
        item = { ...queue[index] };
        queue = queue.filter((_, i) => i !== index);
        tab = "edit";
    }

    function approveQueued(index) {
        if (queue[index]?.reviewAcknowledged !== true) {
            error = "Open this item and review its Needs review findings before approving it for eBay.";
            return;
        }
        queue = queue.map((entry, entryIndex) => entryIndex === index
            ? { ...entry, approved: true, approvedAt: new Date().toISOString() }
            : entry);
        status = "Item approved for the Seller Hub draft queue.";
        scheduleAutoDraftUpload();
    }

    function retryFailedDraftBatch() {
        const submitted = queue.filter((entry) => entry.ebayFeedTaskId);
        if (!submitted.length) {
            error = "There is no submitted Seller Hub batch to retry.";
            return;
        }
        const taskIds = [...new Set(submitted.map((entry) => entry.ebayFeedTaskId))];
        const taskLabel = taskIds.join(", ");
        const confirmed = window.confirm(
            `The selected Seller Hub task(s) ${taskLabel} must already be confirmed failed in eBay's result file. Clear the task marker and make these approved rows sendable again? This does not submit or publish anything.`
        );
        if (!confirmed) return;
        queue = queue.map((entry) => entry.ebayFeedTaskId
            ? { ...entry, ebayFeedTaskId: undefined, ebayDraftStatus: undefined }
            : entry);
        error = "";
        status = `Failed Seller Hub batch ${taskLabel} cleared. Review the approved rows, then send the corrected draft CSV once.`;
    }

    function scheduleAutoDraftUpload() {
        if (!autoDraftEnabled || autoDraftInFlight || draftLoading || approvedDraftQueue().length < 5) return;
        setTimeout(() => {
            if (autoDraftEnabled && !autoDraftInFlight && !draftLoading && approvedDraftQueue().length >= 5) {
                sendDraftQueue({ automatic: true });
            }
        }, 0);
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

    async function sendDraftQueue({ automatic = false } = {}) {
        const candidates = approvedDraftQueue();
        if (!candidates.length) {
            error = "Approve at least five reviewed items before sending Seller Hub drafts.";
            return;
        }
        if (candidates.length < 5) {
            error = `Seller Hub draft upload requires at least 5 approved unique items; ${candidates.length} are ready.`;
            return;
        }
        const invalid = firstInvalidQueuedItem(candidates);
        if (invalid) {
            error = `Queue item ${invalid.index + 1}: ${invalid.message}`;
            item = { ...invalid.reviewed };
            tab = "edit";
            return;
        }
        const confirmed = automatic || window.confirm("Send the approved items to eBay as a Seller Hub draft feed? This submits drafts for processing; it does not publish live listings.");
        if (!confirmed) return;
        error = "";
        draftLoading = true;
        autoDraftInFlight = automatic;
        try {
            const result = await sendDraftFeed(candidates);
            const submitted = new Set(candidates.map((entry) => queueIdentity(entry)));
            queue = queue.map((entry) => submitted.has(queueIdentity(entry))
                ? { ...entry, ebayFeedTaskId: result.taskId, ebayDraftStatus: result.status }
                : entry);
            status = `${automatic ? "Auto-sent" : "Submitted"} ${result.itemCount} approved item${result.itemCount === 1 ? "" : "s"} to eBay Seller Hub Drafts. Task ${result.taskId}. Live listings were not published.`;
            if (result.taskId) await monitorEbayFeedTask(result.taskId, submitted);
        } catch (err) {
            error = friendlyEbayError(err);
        } finally {
            draftLoading = false;
            autoDraftInFlight = false;
        }
    }

    async function monitorEbayFeedTask(taskId, submitted) {
        for (let attempt = 0; attempt < 20; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 1000 : 3000));
            const task = await ebayFeedTask(taskId);
            const terminal = ["COMPLETED", "COMPLETED_WITH_ERROR", "FAILED"].includes(String(task?.status || "").toUpperCase());
            if (!terminal) continue;
            const details = task.resultDetails || null;
            queue = queue.map((entry) => submitted.has(queueIdentity(entry))
                ? { ...entry, ebayDraftStatus: task.status, ebayFeedResultDetails: details }
                : entry);
            const summary = task.uploadSummary || {};
            if (details?.errors?.length) {
                status = `eBay finished task ${taskId}: ${summary.successCount || 0} accepted, ${summary.failureCount || details.errors.length} failed.`;
                error = details.errors.slice(0, 3).map((row) => `${row.code || "eBay error"}: ${row.message}`).join(" | ");
            } else {
                status = `eBay finished task ${taskId}: ${summary.successCount || 0} accepted, ${summary.failureCount || 0} failed.`;
            }
            return;
        }
        status = `eBay task ${taskId} is still processing. Use Refresh or check Seller Hub Reports for the final result.`;
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

    async function loadPerformance() {
        performanceLoading = true;
        try {
            performanceData = await commercePerformance();
            rotationSelected = rotationSelected.filter((id) => (performanceData.rotation || []).some((entry) => entry.id === id));
        } catch (err) { error = err.message || "Performance data is unavailable."; }
        finally { performanceLoading = false; }
    }

    async function waitForCommerceJob(jobId, label) {
        if (!jobId) throw new Error(`${label} did not return a job ID.`);
        const until = Date.now() + 150000;
        while (Date.now() < until) {
            const job = await commerceJob(jobId);
            if (job.status === "completed") return job.result || {};
            if (job.status === "failed") throw new Error(job.error || `${label} failed.`);
            status = `${label} is ${job.status || "queued"}...`;
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        throw new Error(`${label} is still running. Check back shortly.`);
    }

    async function syncPerformance() {
        performanceSyncing = true; error = "";
        try { const job = await commerceStartPerformanceSync(30); await waitForCommerceJob(job.jobId, "Performance sync"); await loadPerformance(); status = "Official eBay traffic metrics refreshed."; }
        catch (err) { error = err.message || "Performance sync failed."; }
        finally { performanceSyncing = false; }
    }

    async function syncFulfillment() {
        try { const job = await commerceStartFulfillmentSync(90); await waitForCommerceJob(job.jobId, "Fulfillment sync"); status = "Seller order history refreshed."; }
        catch (err) { error = err.message || "Fulfillment sync failed."; }
    }

    function toggleRotation(id) { rotationSelected = rotationSelected.includes(id) ? rotationSelected.filter((value) => value !== id) : [...rotationSelected, id]; }

    async function approveRotation() {
        if (!rotationSelected.length) return;
        try { const result = await commerceApproveRotation(rotationSelected); rotationSelected = []; status = `${result.approved} rotation decision${result.approved === 1 ? "" : "s"} approved. No eBay listing status was changed.`; await loadPerformance(); }
        catch (err) { error = err.message || "Rotation approvals failed."; }
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

    function useCurrentItemAsHints() {
        analysisHints = {
            ...analysisHints,
            brand: item.brand === "Not visible" ? "" : (item.brand || ""),
            model: item.model === "Not visible" ? "" : (item.model || ""),
            itemType: item.type === "Not visible" ? "" : (item.type || ""),
            category: item.cat || "",
        };
        tab = "analyze";
        status = "Current listing fields copied as analysis clues. Add search terms, then re-analyze.";
    }

</script>

<AuthGate on:sessionChange={refreshPhotoStorage}>
<div class="shell">
    <header class="topbar">
        <div>
            <h1>HHT eBay Listing Builder</h1>
            <p>Photo analysis, seller review, and Seller Hub draft feed submission</p>
        </div>
        <strong>{queue.length} item{queue.length === 1 ? "" : "s"}</strong>
    </header>

    <nav class="tabs" aria-label="Main navigation">
        <button class:on={tab === "commerce"} on:click={() => tab = "commerce"}>Dashboard</button>
        <button class:on={tab === "analyze"} on:click={() => tab = "analyze"}>Analyze</button>
        <button class:on={tab === "edit"} on:click={() => tab = "edit"}>Review</button>
        <button class:on={tab === "queue"} on:click={() => tab = "queue"}>Queue</button>
        <button class:on={tab === "capacity"} on:click={() => { tab = "capacity"; loadPerformance(); }}>Capacity</button>
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
            {#if breakGroundEnabled && !onboardingDismissed}
                <div class="wide notice info onboarding-card">
                    <div class="section-heading"><div><strong>HHT quick start</strong><p>Group photos, analyze, review the eBay-aligned fields, then add only approved items to the Seller Hub draft queue.</p></div><button type="button" on:click={dismissOnboarding}>Dismiss</button></div>
                    <div class="onboarding-steps"><span class:complete={stagingPhotos.length > 0}>1. Group photos</span><span class:complete={item.title && item.cat && item.price}>2. Review listing</span><span class:complete={queue.length > 0}>3. Save to queue</span><span class:complete={queue.filter((entry) => entry.approved).length >= 5}>4. Send 5 approved drafts</span></div>
                    <p class="help">BreakGround guidance is optional and stays in this app; no checklist events are currently transmitted.</p>
                </div>
            {/if}
            <label class="field">
                <span>Analysis engine</span>
                <select bind:value={engine}>
                    <option value="hosted">Groq first pass + NVIDIA audit</option>
                    <option value="local">Browser-local SmolVLM experimental</option>
                </select>
            </label>
            <p class="help">
                {engine === "hosted"
                    ? "Hosted analysis is queued and returns the Groq result as soon as it is ready. NVIDIA checks for missed fields and visible flaws in parallel; neither provider directly changes eBay."
                    : "The browser downloads an open-source model locally. It may be slow or unsupported on phones."}
            </p>
            {#if analysisJobs.length}
                <div class="wide notice info">
                    <strong>Item analysis jobs</strong>
                    {#if analysisMedianFirstPass !== null}<p>Observed median (uncached): Groq first pass {analysisMedianFirstPass.toFixed(2)}s · full audit {analysisMedianTotal.toFixed(2)}s across {analysisTimingSamples.length} item{analysisTimingSamples.length === 1 ? "" : "s"}. No pre-change baseline is available in this browser.</p>{/if}
                    {#each analysisJobs as job}
                        <div class="queue-row">
                            <div><strong>{job.label || "Listing item"}</strong><span>{job.status} · {job.progress || 0}%</span>
                                {#if job.error}<small>{job.error}</small>{/if}
                                {#if job.result?.auditStatus === "running"}<small>Groq result available; NVIDIA audit is still running.</small>{/if}
                                {#if job.result?.needsReview?.length}<small>Needs review: {job.result.needsReview.map((entry) => entry.reason || entry.field).join("; ")}</small>{/if}
                            </div>
                            {#if job.result}<button type="button" on:click={() => openAnalysisResult(job)}>Review result</button>{/if}
                        </div>
                    {/each}
                </div>
            {/if}
            <div class="wide notice info analysis-guidance">
                <strong>Guide the analysis (optional)</strong>
                <p>Enter a clue when the first result is wrong. These are hypotheses for the vision model to verify—not automatic facts.</p>
                <div class="form-grid compact">
                    <label class="field"><span>Brand or maker</span><input bind:value={analysisHints.brand} placeholder="Example: New Era" maxlength="160" /></label>
                    <label class="field"><span>Model / style / line</span><input bind:value={analysisHints.model} placeholder="Example: 9FIFTY" maxlength="160" /></label>
                    <label class="field"><span>Item type</span><input bind:value={analysisHints.itemType} placeholder="Example: youth snapback hat" maxlength="160" /></label>
                    <label class="field"><span>Category hint or eBay ID</span><input bind:value={analysisHints.category} placeholder="Example: sports hat" maxlength="160" /></label>
                    <label class="field wide"><span>Search terms</span><input bind:value={analysisHints.searchTerms} placeholder="Example: Cleveland Cavaliers, NBA, Hardwood Classics" maxlength="160" /></label>
                </div>
            </div>
            <div
                class:drag-active={dragActive}
                class="dropzone bulk-staging-dropzone"
                role="button"
                tabindex="0"
                on:dragover|preventDefault={() => dragActive = true}
                on:dragleave={() => dragActive = false}
                on:drop={onDrop}
                on:keydown={(event) => (event.key === "Enter" || event.key === " ") && stagingInput?.click()}
                on:click={() => stagingInput?.click()}
            >
                <input bind:this={stagingInput} type="file" accept="image/*" multiple on:click|stopPropagation on:change={onFilesSelected} />
                <strong>Drop 100+ product photos here</strong>
                <span>Or choose a photo folder. Click thumbnails to group up to 3 views for one item.</span>
            </div>
            {#if stagingPhotos.length}
                <div class="staging-toolbar">
                    <div>
                        <strong>{stagingPhotos.length} staged</strong>
                        <span>{stagingPhotos.filter((photo) => !photo.processed).length} available · {stagingPhotos.filter((photo) => photo.processed).length} processed and locked</span>
                    </div>
                    <button type="button" on:click={clearStagingPhotos}>Clear available photos</button>
                </div>
                <div class="bulk-staging-grid" aria-label="Bulk photo staging grid">
                    {#each stagingPhotos as photo}
                        <div class:staging-selected={selectedPhotoIds.includes(photo.id)} class:staging-processed={photo.processed} class="staging-photo">
                            <button type="button" class="staging-photo-button" disabled={photo.processed || loading} on:click={() => toggleStagingPhoto(photo)} aria-label={photo.processed ? `${photo.name} processed` : `Select ${photo.name}`}>
                                <img src={photo.url} alt={photo.name} loading="lazy" />
                                {#if photo.processed}<span class="staging-lock">Processed</span>{:else if selectedPhotoIds.includes(photo.id)}<span class="staging-order">{selectedPhotoIds.indexOf(photo.id) + 1}</span>{/if}
                            </button>
                            <div class="staging-photo-meta"><span title={photo.name}>{photo.name}</span>{#if !photo.processed}<button type="button" on:click={() => removeStagingPhoto(photo)} aria-label={`Remove ${photo.name}`}>×</button>{/if}</div>
                        </div>
                    {/each}
                </div>
                <div class="staging-actions">
                    <span>{selectedStagingPhotos.length ? `${selectedStagingPhotos.length} photo${selectedStagingPhotos.length === 1 ? "" : "s"} selected for one item.` : "Select the photos belonging to one item."}</span>
                    <button class="primary" type="button" disabled={loading || !selectedStagingPhotos.length} on:click={processStagedItem}>{loading ? "Processing item..." : "Process Item with Groq"}</button>
                </div>
            {/if}
            {#if previews.length && !stagingPhotos.length}
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
                <button class="primary" disabled={loading || !files.length || stagingPhotos.length > 0} on:click={analyze}>{loading ? "Analyzing..." : "Analyze photos"}</button>
                <button type="button" on:click={() => setFiles([])}>Clear photos</button>
            </div>
        </section>
    {/if}

    {#if tab === "capacity"}
        <section class="panel">
            <div class="section-heading"><div><p class="eyebrow">LISTING CAP MANAGER</p><h2>Capacity &amp; rotation</h2><p>Use official eBay traffic evidence to decide what to optimize, keep, or review. HHT never changes listing status automatically.</p></div><div class="actions"><button on:click={syncPerformance} disabled={performanceSyncing}>{performanceSyncing ? "Syncing..." : "Sync eBay traffic"}</button><button on:click={syncFulfillment}>Sync orders</button></div></div>
            <div class="metric-grid">
                <div class="metric-card"><span>Active listings</span><strong>{performanceData.capacity?.active ?? "—"}</strong></div>
                <div class="metric-card"><span>Configured maximum</span><strong>{performanceData.capacity?.maximum ?? "Not configured"}</strong></div>
                <div class="metric-card"><span>Remaining</span><strong>{performanceData.capacity?.remaining ?? "—"}</strong></div>
                <div class="metric-card"><span>Evidence date</span><strong>{performanceData.lastSync || "Not synced"}</strong></div>
            </div>
            <div class="notice info"><strong>Source and safety</strong><p>{performanceData.capacity?.note || "Sync traffic to populate official metrics."} Rotation decisions are approval-only. Approving below records your decision; it does not deactivate, reactivate, or publish a listing.</p></div>
            <div class="rotation-list">
                {#if performanceLoading}<p>Loading performance evidence...</p>{:else if !(performanceData.rotation || []).length}<p>No performance snapshots yet. Import active listings, then sync eBay traffic.</p>{:else}
                    {#each performanceData.rotation as entry}
                        <label class="rotation-row"><input type="checkbox" checked={rotationSelected.includes(entry.id)} on:change={() => toggleRotation(entry.id)} /><span><strong>{entry.title}</strong><small>{entry.action} · {entry.reason}</small><small>Impressions {entry.evidence.impressions} · Views {entry.evidence.views} · CTR {Number(entry.evidence.ctr || 0).toFixed(2)}% · Transactions {entry.evidence.transactions}</small></span><b>{entry.risk}</b></label>
                    {/each}
                    <button class="primary" disabled={!rotationSelected.length} on:click={approveRotation}>Approve Actions ({rotationSelected.length})</button>
                {/if}
            </div>
        </section>
    {/if}

    {#if tab === "commerce"}
        <CommerceAgent />
    {/if}

    {#if tab === "edit"}
        <div class="wide notice warn">
            <strong>Needs review ({itemReviewFindings.length})</strong>
            {#if item.auditStatus === "running"}<p>NVIDIA is checking for missed fields, label text, and visible flaws in the background.</p>{/if}
            {#if itemReviewFindings.length}
                {#each itemReviewFindings as finding}<p>{finding}</p>{/each}
            {:else}
                <p>No deterministic issues found. Confirm the values against the photos before adding this item to the queue.</p>
            {/if}
            {#if item.provenance}
                <p class="help">Field evidence: {Object.entries(item.provenance).filter(([field]) => ["brand", "model", "size", "color", "type", "mat", "madeIn"].includes(field)).map(([field, source]) => `${field}: ${source}`).join(" · ")}</p>
            {/if}
        </div>
        {#if reviewNotes.length}
            <div class="notice warn">
                <strong>Seller review required</strong>
                {#each reviewNotes as note}<p>{note}</p>{/each}
            </div>
        {/if}
        <section class="panel form">
            <div class="wide notice info">
                <strong>One listing form for both export methods</strong>
                <p>Complete each fact once in this eBay-aligned form. Seller Hub Draft CSV and the legacy CSV export use the same values automatically; you do not need to re-enter a second set of fields.</p>
                <div class="actions">
                    <button type="button" disabled={loading || !files.length} on:click={() => analyze({ isRerun: true })}>{loading ? "Re-analyzing..." : "Re-analyze with my corrections"}</button>
                    <button type="button" on:click={() => tab = "analyze"}>Edit analysis clues</button>
                    <button type="button" on:click={useCurrentItemAsHints}>Use current fields as clues</button>
                </div>
                <p class="help">Add or correct a brand, model, category, or search term on the Analyze tab, then rerun. Your current result stays in place if the rerun fails.</p>
                {#if item.analysisHintFields?.length}<p class="help">Seller clues used for this result: {item.analysisHintFields.join(", ")}. Confirm them against the photos before export.</p>{/if}
            </div>
            <label class="field wide"><span>Title <em>{titleLength}/80</em></span><input bind:value={item.title} maxlength="80" /></label>
            {#if item.titleCandidates && Object.keys(item.titleCandidates).length}
                <div class="wide notice info">
                    <strong>Title options (SEO-safe)</strong>
                    <p class="help">These suggestions are generated from the extracted listing attributes and capped at 80 characters. Pick one to apply it to the Title field, then verify every detail against the photos.</p>
                    {#if item.titleSeoMetadata}
                        <p class="help">Confirmed-keyword coverage: {Math.round(Number(item.titleSeoMetadata?.keywordCoverage || 0) * 100)}%. Source: {item.titleSeoMetadata?.source || ""}</p>
                    {/if}
                    <div class="category-suggestions" aria-label="Suggested title candidates">
                        {#each Object.entries(item.titleCandidates) as [key, candidate]}
                            <button type="button" on:click={() => item = applyClientRules({ ...item, title: candidate.title })}>
                                <strong>{candidate.title}</strong>
                                <span>{candidate.length}/80 · {candidate.confidence} confidence · {key.replace(/_/g, " ")}</span>
                            </button>
                        {/each}
                    </div>
                </div>
            {/if}
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
                <label class="field"><span>Quick category menu <em>or use search above</em></span><select bind:value={item.cat} on:change={() => { item = applyClientRules(item); loadCategoryFields(item.cat); }}><option value="">Choose a category</option>{#each CATEGORY_OPTIONS as option}<option value={option.value}>{option.label}</option>{/each}</select></label>
                {#if categoryNotice}<p class="help category-message">{categoryNotice}</p>{/if}
                {#if categorySuggestions.length}
                    <div class="category-suggestions" aria-label="eBay category suggestions">
                        {#each categorySuggestions as suggestion}
                            <button type="button" on:click={() => chooseCategory(suggestion)}><strong>{suggestion.categoryName}</strong><span>{suggestion.path} · ID {suggestion.categoryId}</span></button>
                        {/each}
                    </div>
                {/if}
            </div>
            <section class="wide dynamic-aspects ebay-specifics" aria-label="eBay item specifics editor">
                <div class="category-head">
                    <div>
                        <strong>eBay item specifics</strong>
                        <p>These are the only item-detail fields you need to complete. Common fields are mapped into the selected eBay category, and category-specific required fields appear automatically.</p>
                    </div>
                    {#if categoryFields.length}<span>{categoryFields.filter((field) => field.required).length} required for this category</span>{/if}
                </div>
                <div class="form aspect-grid">
                    {#each ebaySpecificFields as field}
                        <div class="field">
                            <span>{field.name} {#if field.required}<em class="required">Required</em>{:else if field.recommended}<em>Recommended</em>{/if}</span>
                            {#if field.multiSelect && field.values?.length}
                                <div class="multi-select-options">
                                    {#each field.values as value}
                                        <label><input type="checkbox" checked={categoryFieldValues(field.name).includes(value)} on:change={(event) => toggleCategoryFieldValue(field.name, value, event.currentTarget.checked)} /> <span>{value}</span></label>
                                    {/each}
                                </div>
                                <small>Choose all that apply.</small>
                            {:else if field.values?.length && field.values.length <= 40}
                                <select value={categoryFieldValue(field.name)} on:change={(event) => updateCategoryField(field.name, event.currentTarget.value)}>
                                    <option value="">Select or leave blank</option>
                                    {#each field.values as value}<option value={value}>{value}</option>{/each}
                                </select>
                            {:else}
                                <input value={categoryFieldValue(field.name)} on:input={(event) => updateCategoryField(field.name, event.currentTarget.value)} placeholder="Enter a seller-confirmed value" />
                            {/if}
                        </div>
                    {/each}
                </div>
                {#if !categoryFields.length && item.cat}<p class="help">Choose or search for the category above to load its live required and recommended aspects.</p>{/if}
            </section>
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
            <div class="notice info">
                <strong>{Math.min(approvedDraftQueue().length, 5)} of 5 approved items ready for Seller Hub Drafts</strong>
                <p>{approvedDraftQueue().length >= 5 ? "Your approved batch is ready. It will send automatically only when Auto-send is enabled; otherwise use Send approved drafts now." : `Approve ${5 - approvedDraftQueue().length} more unique reviewed item${5 - approvedDraftQueue().length === 1 ? "" : "s"} before sending to eBay.`}</p>
            </div>
            {#if !queue.length}
                <p class="empty">Analyze an item, review the fields, then add it here.</p>
            {:else}
                {#each queue as queued, index}
                    <div class="queue-row">
                        <div><strong>{queued.title}</strong><span>{queued.brand} / {queued.size} / ${Number(queued.price || 0).toFixed(2)} · {queued.approved ? "Approved" : "Needs approval"}{queued.ebayFeedTaskId ? ` / feed task ${queued.ebayFeedTaskId} (${queued.ebayDraftStatus || "submitted"})` : ""}</span></div>
                        {#if queued.ebayFeedResultDetails?.errors?.length}
                            <div class="notice error">
                                {#each queued.ebayFeedResultDetails.errors.slice(0, 2) as feedError}
                                    <strong>{feedError.code || "eBay feed error"}</strong>: {feedError.message}
                                    {#if feedError.customLabel}<span> · SKU {feedError.customLabel}</span>{/if}
                                {/each}
                            </div>
                        {/if}
                        {#if !queued.approved}<button type="button" on:click={() => approveQueued(index)}>Approve</button>{/if}
                        <button type="button" on:click={() => editQueued(index)}>Edit</button>
                        <button type="button" on:click={() => queue = queue.filter((_, i) => i !== index)}>Remove</button>
                    </div>
                {/each}
                <div class="actions">
                    <button class="primary" disabled={draftLoading || approvedDraftQueue().length < 5} on:click={() => sendDraftQueue()}>{draftLoading ? "Sending..." : "Send approved drafts now (5+ items)"}</button>
                    {#if queue.some((entry) => entry.ebayFeedTaskId)}
                        <button type="button" on:click={retryFailedDraftBatch}>Retry confirmed failed batch</button>
                    {/if}
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
            <div class="wide notice info">
                <strong>Photo storage</strong>
                <p>{photoStorage.configured ? `Configured: ${photoStorage.provider}. Compressed derivatives can receive signed HTTPS URLs for eBay.` : "Not configured. Analysis still works, but browser-local photos are not yet durable eBay image URLs."}</p>
            </div>
            <label class="field wide checkbox-field">
                <input type="checkbox" bind:checked={autoDraftEnabled} on:change={scheduleAutoDraftUpload} />
                <span><strong>Auto-send approved items to eBay Drafts</strong><small>Off by default. When enabled, HHT sends a batch automatically only after five unique reviewed and approved items are ready. It creates drafts only; it never publishes live listings.</small></span>
            </label>
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
</AuthGate>

<style>
    .bulk-staging-dropzone {
        display: flex;
        min-height: 112px;
        align-items: center;
        justify-content: center;
        text-align: center;
        transition: border-color .15s ease, background .15s ease;
    }

    .bulk-staging-dropzone.drag-active {
        border-color: var(--blue);
        background: #eff6ff;
    }

    .bulk-staging-dropzone input {
        display: none;
    }

    .staging-toolbar,
    .staging-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-top: 12px;
    }

    .staging-toolbar div {
        display: grid;
        gap: 2px;
    }

    .staging-toolbar span,
    .staging-actions span {
        color: var(--muted);
        font-size: 13px;
    }

    .bulk-staging-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
        gap: 10px;
        max-height: 560px;
        margin-top: 12px;
        overflow: auto;
        padding: 2px;
    }

    .staging-photo {
        min-width: 0;
        border: 2px solid transparent;
        border-radius: 10px;
        background: #f8fafc;
        overflow: hidden;
    }

    .staging-photo.staging-selected {
        border-color: var(--blue);
        box-shadow: 0 0 0 2px #bfdbfe;
    }

    .staging-photo.staging-processed {
        border-color: #cbd5e1;
        opacity: .58;
    }

    .staging-photo-button {
        position: relative;
        display: block;
        width: 100%;
        min-height: 112px;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: #e2e8f0;
        overflow: hidden;
    }

    .staging-photo-button img {
        display: block;
        width: 100%;
        height: 112px;
        object-fit: cover;
    }

    .staging-order,
    .staging-lock {
        position: absolute;
        right: 6px;
        top: 6px;
        border-radius: 999px;
        background: var(--blue);
        color: #fff;
        padding: 3px 7px;
        font-size: 11px;
        font-weight: 800;
    }

    .staging-lock {
        background: #475569;
    }

    .staging-photo-meta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 4px;
        padding: 5px 6px;
        font-size: 11px;
    }

    .staging-photo-meta span {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .staging-photo-meta button {
        min-height: 24px;
        padding: 0 5px;
        border: 0;
        background: transparent;
        color: var(--red);
    }

    @media (max-width: 640px) {
        .staging-toolbar,
        .staging-actions {
            align-items: stretch;
            flex-direction: column;
        }

        .staging-toolbar button,
        .staging-actions button {
            width: 100%;
        }
    }
</style>
