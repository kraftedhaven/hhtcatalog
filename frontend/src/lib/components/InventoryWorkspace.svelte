<script>
    import { onMount, onDestroy } from "svelte";
    import {
        inventoryCreateBatch,
        inventoryListBatches,
        inventoryGetBatch,
        inventoryDeleteBatch,
        inventoryUploadBatchPhotos,
        inventoryUpdatePhotoGrouping,
        inventoryListBatchItems,
        inventoryAnalyzeBatch,
        inventoryGetItem,
        inventoryPatchItem,
        inventoryEnrichFromResearch,
        inventoryValidateItems,
        inventoryApproveItems,
        inventorySubmitDrafts,
        inventoryGetItemSubmission,
        inventoryExportBatchDraftCSV,
        ebayCategorySuggestions,
        ebayCategoryAspects,
    } from "$lib/api";
    import {
        formatBytes,
        prepareUploadBatch,
        partitionUploadChunks,
    } from "$lib/image-compressor";

    export let onSwitchToListingOptimization = () => {};
    export let onOpenFullReview = () => {};
    export let onOpenAnalysisMonitor = () => {};

    // Workspace modes: 'new_inventory' | 'listing_optimization'
    let activeSubTab = "new_inventory";

    // Batch state
    let batches = [];
    let selectedBatchId = "";
    let selectedBatch = null;
    let batchLoading = false;

    // Batch actions & template states
    let isBatchActionsMenuOpen = false;
    let sellerTemplateCsv = "";
    let templateFileName = "";
    let isTemplateModalOpen = false;
    let exportCsvLoading = false;

    // View mode: 'cards' (MetaMuse-style visual listing cards, default) | 'table' (11-column CSV draft verification)
    let viewMode = "cards";
    let isDraftResultModalOpen = false;
    let lastDraftResult = null;

    // Category aspects state for inspecting item
    let categoryAspects = [];
    let categoryAspectsLoading = false;
    let categoryAspectsError = "";
    let lastLoadedAspectCategoryId = "";
    let categoryQuery = "";
    let categorySuggestions = [];
    let categorySearching = false;

    // Items and selection
    let items = [];
    let selectedItemIds = new Set();
    let inspectingItem = null;
    let inspectingItemId = "";
    let researchClues = {
        observedTitle: "",
        brand: "",
        itemType: "",
        model: "",
        category: "",
        size: "",
        color: "",
        comparablePrice: "",
    };
    let researchLoading = false;

    // Reactive Batch Lifecycle Counts
    $: totalItemCount = items.length;
    $: analyzingCount = items.filter(
        (it) => it.workflowStatus === "analysis_queued" || it.workflowStatus === "analysis_running"
    ).length;
    $: readyCount = items.filter((it) => it.workflowStatus === "ready_for_approval").length;
    $: needsReviewCount = items.filter((it) => it.workflowStatus === "needs_review").length;
    $: approvedCount = items.filter((it) => it.workflowStatus === "approved").length;
    $: submittedCount = items.filter(
        (it) => it.workflowStatus === "draft_submitted" || it.workflowStatus === "draft_result_available"
    ).length;

    // State-Dependent Action Analysis
    $: relevantItems = selectedItemIds.size > 0
        ? items.filter((it) => selectedItemIds.has(it.id))
        : items;
    $: hasAnalyzing = relevantItems.some(
        (it) => it.workflowStatus === "analysis_queued" || it.workflowStatus === "analysis_running"
    );
    $: hasGrouped = relevantItems.some(
        (it) => it.workflowStatus === "grouped" || it.workflowStatus === "photo_staged"
    );
    $: hasMissingRequired = relevantItems.some((it) => {
        const l = it.listing || {};
        return !l.title || !l.price || !l.cat;
    });
    $: hasNeedsReview = relevantItems.some((it) => it.workflowStatus === "needs_review");
    $: hasValidated = relevantItems.some(
        (it) => it.rulesCheck?.valid && it.workflowStatus !== "approved"
    );
    $: allApproved = relevantItems.length > 0 && relevantItems.every((it) => it.workflowStatus === "approved");
    $: allSubmitted = relevantItems.length > 0 && relevantItems.every(
        (it) => it.workflowStatus === "draft_submitted" || it.workflowStatus === "draft_result_available"
    );

    // Filters and sorting
    let searchQuery = "";
    let statusFilter = "all"; // all, incomplete, needs_review, ready_for_approval, approved, draft_submitted
    let sortBy = "created_at"; // created_at, title, price, status
    let sortDirection = "asc";

    // Autosave engine
    let autosaveStatus = "idle"; // idle, saving, saved, error
    let autosaveTimeout = null;
    let pendingPatches = new Map(); // itemId -> { field: value }

    // Upload & Grouping Drawer / Modal
    let isUploadOpen = false;
    let uploadFiles = [];
    let uploadPreviews = [];
    let uploadGroupingMode = "3"; // 1, 2, 3, auto
    let isUploading = false;
    let uploadPhase = "idle"; // idle, compressing, uploading
    let uploadPhaseMessage = "";
    let uploadError = "";

    // Global action states
    let globalStatus = "";
    let globalError = "";
    let actionLoading = false;

    // Title & Pricing candidate picker popovers
    let activeTitleCandidatesItemId = null;
    let activePriceTiersItemId = null;

    // Relative time formatter
    function formatTime(iso) {
        if (!iso) return "Never";
        try {
            const date = new Date(iso);
            const diff = Math.floor((Date.now() - date.getTime()) / 1000);
            if (diff < 60) return "Just now";
            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
            return date.toLocaleDateString();
        } catch {
            return iso;
        }
    }

    onMount(async () => {
        await loadBatches();
    });

    onDestroy(() => {
        if (autosaveTimeout) clearTimeout(autosaveTimeout);
    });

    async function loadBatches() {
        batchLoading = true;
        globalError = "";
        try {
            batches = await inventoryListBatches();
            if (batches.length > 0) {
                if (
                    !selectedBatchId ||
                    !batches.some((b) => b.id === selectedBatchId)
                ) {
                    selectedBatchId = batches[0].id;
                }
                await selectBatch(selectedBatchId);
            } else {
                // Auto create initial batch if none exists
                const newBatch = await inventoryCreateBatch(
                    "First Inventory Batch",
                    "3",
                );
                batches = [newBatch];
                selectedBatchId = newBatch.id;
                await selectBatch(selectedBatchId);
            }
        } catch (err) {
            globalError = err.message || "Unable to load inventory batches.";
        } finally {
            batchLoading = false;
        }
    }

    async function selectBatch(batchId) {
        selectedBatchId = batchId;
        batchLoading = true;
        selectedItemIds = new Set();
        inspectingItem = null;
        inspectingItemId = "";
        try {
            selectedBatch =
                batches.find((b) => b.id === batchId) ||
                (await inventoryGetBatch(batchId));
            items = await inventoryListBatchItems(batchId);
        } catch (err) {
            globalError = err.message || "Failed to load batch items.";
        } finally {
            batchLoading = false;
        }
    }

    async function createNewBatch() {
        const name = window.prompt(
            "Enter a name for the new inventory batch:",
            `Batch ${new Date().toLocaleDateString()}`,
        );
        if (!name) return;
        batchLoading = true;
        try {
            const newBatch = await inventoryCreateBatch(name, "3");
            batches = [newBatch, ...batches];
            await selectBatch(newBatch.id);
            globalStatus = `Batch "${name}" created.`;
        } catch (err) {
            globalError = err.message || "Failed to create batch.";
        } finally {
            batchLoading = false;
        }
    }

    async function deleteSelectedBatch() {
        if (!selectedBatchId || !selectedBatch) return;
        const hasSubmitted = items.some(
            (it) => it.workflowStatus === "draft_submitted" || it.workflowStatus === "draft_result_available"
        );
        if (hasSubmitted) {
            globalError = "Cannot delete batch: draft items have already been submitted to eBay.";
            return;
        }
        const confirmed = window.confirm(
            `Delete batch "${selectedBatch.name}" and its grouped photos? This cannot be undone.`
        );
        if (!confirmed) return;
        batchLoading = true;
        globalError = "";
        try {
            await inventoryDeleteBatch(selectedBatchId);
            selectedBatchId = "";
            selectedBatch = null;
            items = [];
            batches = await inventoryListBatches();
            if (batches.length) {
                await selectBatch(batches[0].id);
            } else {
                const newBatch = await inventoryCreateBatch("First Inventory Batch", "3");
                batches = [newBatch];
                await selectBatch(newBatch.id);
            }
            globalStatus = "Batch deleted. You can start over with a clean batch.";
        } catch (err) {
            globalError = err.message || "Unable to delete this batch.";
        } finally {
            batchLoading = false;
        }
    }

    async function loadCategoryAspects(categoryId) {
        const catId = String(categoryId || "").trim();
        if (!catId) {
            categoryAspects = [];
            categoryAspectsError = "";
            lastLoadedAspectCategoryId = "";
            return;
        }
        if (catId === lastLoadedAspectCategoryId && categoryAspects.length > 0) {
            return;
        }
        categoryAspectsLoading = true;
        categoryAspectsError = "";
        try {
            const res = await ebayCategoryAspects(catId);
            if (res.status === "unavailable" || res.status === "not_configured") {
                categoryAspects = [];
                categoryAspectsError = res.message || "eBay category options temporarily unavailable.";
            } else {
                categoryAspects = res.fields || [];
                lastLoadedAspectCategoryId = catId;
                if (!categoryAspects.length && res.message) {
                    categoryAspectsError = res.message;
                }
            }
        } catch (err) {
            categoryAspectsError = "eBay category options temporarily unavailable.";
        } finally {
            categoryAspectsLoading = false;
        }
    }

    $: if (inspectingItem && inspectingItem.listing?.cat) {
        loadCategoryAspects(inspectingItem.listing.cat);
    }

    async function searchCategories(query) {
        if (!query || query.trim().length < 2) {
            categorySuggestions = [];
            return;
        }
        categorySearching = true;
        try {
            const res = await ebayCategorySuggestions(query.trim());
            categorySuggestions = res.suggestions || [];
        } catch {
            categorySuggestions = [];
        } finally {
            categorySearching = false;
        }
    }

    async function selectCategorySuggestion(item, suggestion) {
        handleInlineEdit(item, "cat", suggestion.categoryId);
        handleInlineEdit(item, "categoryName", suggestion.path || suggestion.categoryName);
        categorySuggestions = [];
        categoryQuery = "";
        await loadCategoryAspects(suggestion.categoryId);
    }

    function getAspectValue(item, aspectName) {
        if (!item?.listing) return "";
        const specifics = item.listing.itemSpecifics || {};
        for (const [k, v] of Object.entries(specifics)) {
            if (k.toLowerCase() === aspectName.toLowerCase()) return v;
        }
        const aliases = {
            brand: "brand",
            size: "size",
            color: "color",
            department: "dept",
            type: "type",
            style: "style",
            material: "mat",
            pattern: "pat",
            "country/region of manufacture": "madeIn",
        };
        const canonicalKey = aliases[aspectName.toLowerCase()];
        if (canonicalKey && item.listing[canonicalKey]) {
            return item.listing[canonicalKey];
        }
        return "";
    }

    function setAspectValue(item, aspectName, value) {
        const specifics = { ...(item.listing?.itemSpecifics || {}) };
        specifics[aspectName] = value;
        handleInlineEdit(item, "itemSpecifics", specifics);

        const aliases = {
            brand: "brand",
            size: "size",
            color: "color",
            department: "dept",
            type: "type",
            style: "style",
            material: "mat",
            pattern: "pat",
            "country/region of manufacture": "madeIn",
        };
        const canonicalKey = aliases[aspectName.toLowerCase()];
        if (canonicalKey) {
            handleInlineEdit(item, canonicalKey, Array.isArray(value) ? value.join(" | ") : value);
        }
        handleInlineEdit(item, "confirmField", aspectName);
    }

    function toggleMultiAspectValue(item, aspectName, val) {
        const current = getAspectValue(item, aspectName);
        let list = Array.isArray(current)
            ? [...current]
            : String(current || "").split("|").map((s) => s.trim()).filter(Boolean);
        if (list.includes(val)) {
            list = list.filter((v) => v !== val);
        } else {
            list.push(val);
        }
        setAspectValue(item, aspectName, list);
    }

    function aspectListId(aspectName) {
        return `ebay-aspect-${String(aspectName || "field")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")}`;
    }

    function setCondition(item, value) {
        const normalized = value === "1000" ? "1000" : "3000";
        handleInlineEdit(item, "cid", normalized);
        handleInlineEdit(item, "cid_condition", normalized);
        handleInlineEdit(item, "condition", normalized === "1000" ? "New" : "Pre-owned");
        handleInlineEdit(item, "confirmField", "condition");
    }

    function getAspectProvenance(item, aspect) {
        const val = getAspectValue(item, aspect.name);
        const prov = item.provenance?.[aspect.name] || item.provenance?.[aspect.name.toLowerCase()] || {};
        if (prov.sellerConfirmed || item.sellerConfirmed) {
            return { label: "Seller confirmed", color: "bg-emerald-100 text-emerald-800 border-emerald-300" };
        }
        if (!val || String(val).trim() === "") {
            if (aspect.required) {
                return { label: "Required — missing", color: "bg-rose-100 text-rose-800 border-rose-300 font-bold" };
            }
            if (aspect.recommended) {
                return { label: "Recommended", color: "bg-slate-100 text-slate-600 border-slate-300" };
            }
            return { label: "Optional", color: "bg-gray-100 text-gray-500 border-gray-200" };
        }
        if (prov.source === "photo") {
            return { label: "Auto-filled from photo", color: "bg-blue-100 text-blue-800 border-blue-300" };
        }
        if (prov.source === "clue") {
            return { label: "Auto-filled from seller clue", color: "bg-purple-100 text-purple-800 border-purple-300" };
        }
        if (prov.source === "default") {
            return { label: "Seller default", color: "bg-slate-100 text-slate-700 border-slate-300" };
        }
        return { label: "Auto-filled from photo", color: "bg-blue-100 text-blue-800 border-blue-300" };
    }

    function getItemReadiness(item) {
        if (!item) return { ready: false, percent: 0, missing: [] };
        const l = item.listing || {};
        const missing = [];
        if (!item.photos || item.photos.length === 0) missing.push("Photos");
        if (!l.title || !l.title.trim()) missing.push("Title");
        if (!l.price || Number(l.price) <= 0) missing.push("Price");
        if (!l.cat) missing.push("Category");
        if (item.rulesCheck?.missingRequiredAspects?.length) {
            missing.push(...item.rulesCheck.missingRequiredAspects.map((a) => `Aspect: ${a}`));
        }
        const totalChecks = 4 + (item.rulesCheck?.requiredAspects?.length || 0);
        const passedChecks = Math.max(0, totalChecks - missing.length);
        const percent = Math.max(15, Math.min(100, Math.round((passedChecks / Math.max(1, totalChecks)) * 100)));
        const isApproved = item.workflowStatus === "approved";
        const isSubmitted = item.workflowStatus === "draft_submitted" || item.workflowStatus === "draft_result_available";
        const isReady = isApproved || isSubmitted || (missing.length === 0 && (item.workflowStatus === "ready_for_approval" || item.rulesCheck?.valid));
        return {
            ready: isReady,
            percent: isReady ? 100 : percent,
            missing,
        };
    }

    async function approveSingleItem(item) {
        if (!item || !item.id) return;
        actionLoading = true;
        globalError = "";
        try {
            await flushPendingPatches();
            const res = await inventoryApproveItems([item.id]);
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId === item.id) {
                inspectingItem = items.find((it) => it.id === item.id) || null;
            }
            if (res.approvedCount > 0) {
                globalStatus = `Item approved and ready for eBay Seller Hub draft creation.`;
            } else if (res.rejectedCount > 0) {
                globalError = `Item requires seller verification of title, price, or category before approval.`;
            }
        } catch (err) {
            globalError = err.message || "Approval failed.";
        } finally {
            actionLoading = false;
        }
    }

    async function handleDownloadDraftCsv() {
        const approvedItems = items.filter((it) => it.workflowStatus === "approved");
        if (approvedItems.length === 0) {
            globalError = "No approved items to export. Validate and approve items first.";
            return;
        }
        exportCsvLoading = true;
        globalError = "";
        try {
            const itemIds = selectedItemIds.size > 0
                ? Array.from(selectedItemIds).filter((id) => {
                    const it = items.find((i) => i.id === id);
                    return it && it.workflowStatus === "approved";
                })
                : approvedItems.map((it) => it.id);
            if (itemIds.length === 0) {
                globalError = "None of the selected items are approved. Approve items before CSV export.";
                return;
            }
            await inventoryExportBatchDraftCSV(selectedBatchId, itemIds, sellerTemplateCsv || null);
            globalStatus = `Exported official eBay Seller Hub Create Drafts CSV with ${itemIds.length} approved item(s). Upload via eBay Seller Hub → Reports → Upload.`;
        } catch (err) {
            if (err.validationErrors?.length) {
                const first = err.validationErrors[0];
                globalError = `CSV Validation Failed: Row ${first.row} (${first.sku || first.title || "Item"}): ${first.field} — ${first.message}`;
            } else {
                globalError = err.message || "Failed to export Seller Hub Draft CSV.";
            }
        } finally {
            exportCsvLoading = false;
        }
    }

    function handleTemplateFileUpload(event) {
        const file = event.target.files?.[0];
        if (!file) return;
        templateFileName = file.name;
        const reader = new FileReader();
        reader.onload = (e) => {
            sellerTemplateCsv = e.target.result;
            globalStatus = `Configured official eBay Create Drafts template: ${file.name}.`;
            isTemplateModalOpen = false;
        };
        reader.readAsText(file);
    }

    async function analyzeSelectedOrBatch() {
        const ids = selectedItemIds.size > 0
            ? Array.from(selectedItemIds)
            : items.map((it) => it.id);
        if (!ids.length) return;
        actionLoading = true;
        globalError = "";
        try {
            await inventoryAnalyzeBatch(selectedBatchId, ids);
            items = await inventoryListBatchItems(selectedBatchId);
            globalStatus = `Analysis queued for ${ids.length} item(s). Vision worker is populating titles and price estimates.`;
        } catch (err) {
            globalError = err.message || "Failed to start analysis.";
        } finally {
            actionLoading = false;
        }
    }

    async function reprocessBatch() {
        const unconfirmedIds = items
            .filter((it) => !it.sellerConfirmed && it.workflowStatus !== "draft_submitted" && it.workflowStatus !== "draft_result_available")
            .map((it) => it.id);
        if (!unconfirmedIds.length) {
            globalStatus = "All items in this batch are already seller-confirmed or submitted.";
            return;
        }
        actionLoading = true;
        globalError = "";
        try {
            await inventoryAnalyzeBatch(selectedBatchId, unconfirmedIds);
            items = await inventoryListBatchItems(selectedBatchId);
            globalStatus = `Reprocessing ${unconfirmedIds.length} unconfirmed item(s). Seller-confirmed edits are preserved.`;
        } catch (err) {
            globalError = err.message || "Reprocessing failed.";
        } finally {
            actionLoading = false;
        }
    }

    function fixHighlightedFields() {
        const target = items.find((it) => {
            const l = it.listing || {};
            return !l.title || !l.price || !l.cat || it.workflowStatus === "needs_review";
        });
        if (target) {
            openItemInspector(target);
            globalStatus = `Inspect item ${target.id.slice(0, 8)} to complete missing fields.`;
        }
    }

    function reviewSelectedOrFirst() {
        const target = (selectedItemIds.size > 0 ? items.find((it) => selectedItemIds.has(it.id)) : null) ||
            items.find((it) => it.workflowStatus === "needs_review") ||
            items[0];
        if (target) {
            openItemInspector(target);
        }
    }

    function viewDraftResult() {
        isDraftResultModalOpen = true;
    }

    // -----------------------------------------------------------------------
    // Inline Editing & Debounced Autosave
    // -----------------------------------------------------------------------
    function handleInlineEdit(item, field, value) {
        // Optimistically update locally
        item.listing[field] = value;
        item.updatedAt = new Date().toISOString();
        items = [...items];

        if (inspectingItem && inspectingItem.id === item.id) {
            inspectingItem = { ...item };
        }

        // Queue patch
        const currentPatch = pendingPatches.get(item.id) || {};
        currentPatch[field] = value;
        pendingPatches.set(item.id, currentPatch);

        autosaveStatus = "saving";
        if (autosaveTimeout) clearTimeout(autosaveTimeout);
        autosaveTimeout = setTimeout(flushPendingPatches, 450);
    }

    async function flushPendingPatches() {
        if (pendingPatches.size === 0) {
            autosaveStatus = "idle";
            return;
        }
        autosaveStatus = "saving";
        const entries = Array.from(pendingPatches.entries());
        pendingPatches.clear();

        try {
            for (const [itemId, patchData] of entries) {
                const updated = await inventoryPatchItem(itemId, patchData);
                // Update in items list
                const idx = items.findIndex((it) => it.id === itemId);
                if (idx !== -1) {
                    items[idx] = updated;
                }
                if (inspectingItem && inspectingItem.id === itemId) {
                    inspectingItem = updated;
                }
            }
            items = [...items];
            autosaveStatus = "saved";
            setTimeout(() => {
                if (autosaveStatus === "saved") autosaveStatus = "idle";
            }, 2500);
        } catch (err) {
            autosaveStatus = "error";
            globalError = `Autosave failed: ${err.message}. Retrying on next change.`;
        }
    }

    // -----------------------------------------------------------------------
    // Filter & Sort Logic
    // -----------------------------------------------------------------------
    $: filteredItems = items
        .filter((it) => {
            if (statusFilter === "incomplete") {
                const listing = it.listing || {};
                return (
                    !listing.title ||
                    !listing.price ||
                    !listing.cat ||
                    !listing.size
                );
            }
            if (statusFilter !== "all" && it.workflowStatus !== statusFilter) {
                return false;
            }
            if (!searchQuery) return true;
            const q = searchQuery.toLowerCase();
            const l = it.listing || {};
            return (
                (l.title || "").toLowerCase().includes(q) ||
                (l.brand || "").toLowerCase().includes(q) ||
                (l.cat || "").toLowerCase().includes(q)
            );
        })
        .sort((a, b) => {
            let valA, valB;
            if (sortBy === "title") {
                valA = (a.listing?.title || "").toLowerCase();
                valB = (b.listing?.title || "").toLowerCase();
            } else if (sortBy === "price") {
                valA = Number(a.listing?.price) || 0;
                valB = Number(b.listing?.price) || 0;
            } else if (sortBy === "status") {
                valA = a.workflowStatus;
                valB = b.workflowStatus;
            } else {
                valA = a.createdAt;
                valB = b.createdAt;
            }
            if (valA < valB) return sortDirection === "asc" ? -1 : 1;
            if (valA > valB) return sortDirection === "asc" ? 1 : -1;
            return 0;
        });

    function toggleSort(col) {
        if (sortBy === col) {
            sortDirection = sortDirection === "asc" ? "desc" : "asc";
        } else {
            sortBy = col;
            sortDirection = "asc";
        }
    }

    // -----------------------------------------------------------------------
    // Selection Helpers
    // -----------------------------------------------------------------------
    function toggleSelectAll() {
        if (
            selectedItemIds.size === filteredItems.length &&
            filteredItems.length > 0
        ) {
            selectedItemIds = new Set();
        } else {
            selectedItemIds = new Set(filteredItems.map((it) => it.id));
        }
    }

    function toggleSelectItem(id) {
        if (selectedItemIds.has(id)) {
            selectedItemIds.delete(id);
        } else {
            selectedItemIds.add(id);
        }
        selectedItemIds = new Set(selectedItemIds);
    }

    function openItemInspector(item) {
        inspectingItemId = item.id;
        inspectingItem = item;
        categoryQuery = item.listing?.categoryQuery || item.listing?.categoryName || "";
        researchClues = {
            ...researchClues,
            ...(item.listing?.researchClues || {}),
        };
    }

    function closeItemInspector() {
        inspectingItemId = "";
        inspectingItem = null;
    }

    async function generateResearchSuggestions() {
        if (!inspectingItem) return;
        researchLoading = true;
        globalError = "";
        try {
            const updated = await inventoryEnrichFromResearch(
                inspectingItem.id,
                researchClues,
            );
            const index = items.findIndex((item) => item.id === updated.id);
            if (index !== -1) items[index] = updated;
            items = [...items];
            inspectingItem = updated;
            globalStatus =
                "Research clues saved. Title candidates and price options are ready for review.";
        } catch (err) {
            globalError =
                err.message || "Could not generate research suggestions.";
        } finally {
            researchLoading = false;
        }
    }

    async function analyzeWorkspaceItem(itemId) {
        if (!selectedBatchId || !itemId) return;
        actionLoading = true;
        globalError = "";
        try {
            const result = await inventoryAnalyzeBatch(selectedBatchId, [itemId]);
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId === itemId) {
                inspectingItem = items.find((item) => item.id === itemId) || inspectingItem;
            }
            globalStatus = "Photo analysis queued. HHT will populate the title and estimated price when it finishes.";
            return result;
        } catch (err) {
            globalError = err.message || "Could not queue photo analysis.";
        } finally {
            actionLoading = false;
        }
    }

    // -----------------------------------------------------------------------
    // Batch Actions: Validate, Approve, Submit
    // -----------------------------------------------------------------------
    async function validateSelected() {
        const ids = Array.from(selectedItemIds);
        if (!ids.length) {
            globalError = "Select at least one item to validate.";
            return;
        }
        actionLoading = true;
        globalError = "";
        try {
            await flushPendingPatches();
            const res = await inventoryValidateItems(ids);
            // Refresh batch items
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId) {
                inspectingItem =
                    items.find((it) => it.id === inspectingItemId) || null;
            }
            globalStatus = `Validated ${res.validatedCount} items. Review any flags before approval.`;
        } catch (err) {
            globalError = err.message || "Validation failed.";
        } finally {
            actionLoading = false;
        }
    }

    async function approveSelected() {
        const readyItems = items.filter(
            (it) => it.rulesCheck?.valid || it.workflowStatus === "ready_for_approval" || it.workflowStatus === "needs_review"
        );
        const ids = selectedItemIds.size > 0
            ? Array.from(selectedItemIds)
            : readyItems.map((it) => it.id);
        if (!ids.length) {
            globalError = "No items ready to approve. Validate items first.";
            return;
        }
        actionLoading = true;
        globalError = "";
        try {
            await flushPendingPatches();
            const res = await inventoryApproveItems(ids);
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId) {
                inspectingItem =
                    items.find((it) => it.id === inspectingItemId) || null;
            }
            if (res.rejectedCount > 0) {
                globalError = `${res.rejectedCount} item(s) could not be approved due to validation errors or missing required fields.`;
            }
            if (res.approvedCount > 0) {
                globalStatus = `Approved ${res.approvedCount} item(s). They are ready for Seller Hub draft submission.`;
            }
        } catch (err) {
            globalError = err.message || "Approval failed.";
        } finally {
            actionLoading = false;
        }
    }

    async function submitSelectedDrafts() {
        const approvedBatchItems = items.filter((it) => it.workflowStatus === "approved");
        const ids = selectedItemIds.size > 0
            ? Array.from(selectedItemIds).filter((id) => {
                const it = items.find((i) => i.id === id);
                return it && it.workflowStatus === "approved";
            })
            : approvedBatchItems.map((it) => it.id);

        if (!ids.length) {
            globalError = "No approved items to submit. Validate and approve items before creating eBay drafts.";
            return;
        }

        const confirmed = window.confirm(
            `Submit ${ids.length} approved item(s) to eBay Seller Hub draft feed? No live listings will be created.`,
        );
        if (!confirmed) return;

        actionLoading = true;
        globalError = "";
        try {
            const res = await inventorySubmitDrafts(ids);
            lastDraftResult = {
                ...res,
                submittedCount: res.submittedCount || ids.length,
                feedTaskId: res.feedTaskId,
            };
            isDraftResultModalOpen = true;
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId) {
                inspectingItem =
                    items.find((it) => it.id === inspectingItemId) || null;
            }
            globalStatus = `Submitted ${res.submittedCount || ids.length} draft(s) to eBay Seller Hub! Task ID: ${res.feedTaskId || 'N/A'}.`;
        } catch (err) {
            globalError = err.message || "Seller Hub draft submission failed.";
        } finally {
            actionLoading = false;
        }
    }

    // -----------------------------------------------------------------------
    // Upload & Grouping Drawer Logic
    // -----------------------------------------------------------------------
    function openUploadModal() {
        uploadFiles = [];
        uploadPreviews = [];
        uploadError = "";
        uploadPhase = "idle";
        uploadPhaseMessage = "";
        isUploadOpen = true;
    }

    function closeUploadModal() {
        isUploadOpen = false;
        uploadPreviews.forEach((p) => {
            if (p.url) URL.revokeObjectURL(p.url);
        });
        uploadFiles = [];
        uploadPreviews = [];
        uploadPhase = "idle";
        uploadPhaseMessage = "";
    }

    function handleFilesSelected(event) {
        const files = Array.from(event.target.files || []);
        addUploadFiles(files);
    }

    function handleDrop(event) {
        event.preventDefault();
        const files = Array.from(event.dataTransfer?.files || []);
        addUploadFiles(files);
    }

    function addUploadFiles(files) {
        const imageFiles = files.filter((f) => f.type.startsWith("image/"));
        if (!imageFiles.length) {
            uploadError = "Please select valid image files.";
            return;
        }
        uploadFiles = [...uploadFiles, ...imageFiles];
        const newPreviews = imageFiles.map((file) => ({
            id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
            file,
            name: file.name,
            originalSize: file.size,
            formattedOriginalSize: formatBytes(file.size),
            uploadSize: null,
            formattedUploadSize:
                file.size > 8 * 1024 * 1024
                    ? "Auto-compressing (<8MB)"
                    : formatBytes(file.size),
            isOversized: file.size > 8 * 1024 * 1024,
            url: URL.createObjectURL(file),
        }));
        uploadPreviews = [...uploadPreviews, ...newPreviews];
        uploadError = "";
    }

    async function submitUploadAndGroup() {
        if (!uploadFiles.length) {
            uploadError = "Upload at least one image.";
            return;
        }
        isUploading = true;
        uploadError = "";
        uploadPhase = "compressing";
        uploadPhaseMessage = "";
        const existingItemIds = new Set(items.map((item) => item.id));

        try {
            // Partition into bounded chunks matching grouping mode to ensure request body stays under limit
            const chunks = partitionUploadChunks(
                uploadFiles,
                uploadGroupingMode,
            );
            for (let cIdx = 0; cIdx < chunks.length; cIdx++) {
                const chunk = chunks[cIdx];
                uploadPhase = "compressing";
                uploadPhaseMessage =
                    chunks.length > 1
                        ? `Compressing batch ${cIdx + 1} of ${chunks.length}...`
                        : `Compressing ${chunk.length} photo(s)...`;

                // Compress in browser before sending FormData
                const prepared = await prepareUploadBatch(chunk, {
                    onProgress: (p) => {
                        uploadPhaseMessage = `Compressing ${p.fileName} (${p.current} of ${p.total})...`;
                    },
                });

                // Update preview cards with exact compressed sizes
                for (const item of prepared.items) {
                    const prev = uploadPreviews.find(
                        (p) => p.name === item.name,
                    );
                    if (prev) {
                        prev.uploadSize = item.compressedSize;
                        prev.formattedUploadSize = formatBytes(
                            item.compressedSize,
                        );
                    }
                }
                uploadPreviews = [...uploadPreviews];

                // Upload chunk
                uploadPhase = "uploading";
                uploadPhaseMessage =
                    chunks.length > 1
                        ? `Uploading batch ${cIdx + 1} of ${chunks.length} (${formatBytes(prepared.totalCompressedSize)})...`
                        : `Uploading ${chunk.length} photo(s) (${formatBytes(prepared.totalCompressedSize)})...`;

                await inventoryUploadBatchPhotos(
                    selectedBatchId,
                    prepared.files,
                );
            }

            // Refresh items (backend handles initial grouping or safely appends new item groups without touching existing items)
            items = await inventoryListBatchItems(selectedBatchId);
            const newItemIds = items
                .filter((item) => !existingItemIds.has(item.id))
                .map((item) => item.id);
            if (newItemIds.length) {
                await inventoryAnalyzeBatch(selectedBatchId, newItemIds);
                items = await inventoryListBatchItems(selectedBatchId);
            }
            closeUploadModal();
            globalStatus = newItemIds.length
                ? `Uploaded and grouped ${uploadFiles.length} photo(s). AI title and price analysis is now running.`
                : `Successfully uploaded ${uploadFiles.length} photo(s) (under 10MB limit) and updated item groups.`;
        } catch (err) {
            uploadError = err.message || "Failed to upload and group photos.";
        } finally {
            isUploading = false;
            uploadPhase = "idle";
            uploadPhaseMessage = "";
        }
    }

    async function regroupBatchPhotos(mode, force = false) {
        const hasProtected = items.some(
            (it) =>
                (it.workflowStatus &&
                    it.workflowStatus !== "grouped" &&
                    it.workflowStatus !== "photo_staged") ||
                Boolean(it.analysisJobId) ||
                Boolean(it.sellerConfirmed) ||
                Boolean(it.listing?.title) ||
                Boolean(it.listing?.price),
        );
        if (hasProtected && !force) {
            const confirmed = window.confirm(
                "Warning: This batch contains items with active edits, analysis, or approvals.\n\n" +
                    "Regrouping will adjust photo allocations across items. Existing listing details will be preserved wherever possible, but item groupings will change.\n\n" +
                    "Do you want to proceed with regrouping?",
            );
            if (!confirmed) return;
            force = true;
        }

        actionLoading = true;
        globalError = "";
        try {
            await inventoryUpdatePhotoGrouping(selectedBatchId, {
                mode,
                force,
            });
            items = await inventoryListBatchItems(selectedBatchId);
            globalStatus = `Photos regrouped (${mode} per item).`;
        } catch (err) {
            globalError = err.message || "Failed to regroup photos.";
        } finally {
            actionLoading = false;
        }
    }

    // -----------------------------------------------------------------------
    // Title Candidate & Pricing Option Pickers
    // -----------------------------------------------------------------------
    function selectTitleCandidate(item, candidateKey) {
        handleInlineEdit(item, "selectedTitleCandidate", candidateKey);
        activeTitleCandidatesItemId = null;
    }

    function selectPricingOption(item, optionKey) {
        handleInlineEdit(item, "selectedPricingOption", optionKey);
        activePriceTiersItemId = null;
    }

    function confirmPrice(item) {
        handleInlineEdit(item, "confirmField", "price");
    }

    function confirmAllFields(item) {
        handleInlineEdit(item, "confirmAll", true);
    }

    // Helper to get status badge classes matching Content 16 requirements
    function getStatusBadge(status, item = null) {
        switch (status) {
            case "photo_staged":
                return {
                    text: "Uploading photos…",
                    bg: "bg-indigo-50 text-indigo-700 border-indigo-200",
                };
            case "grouped":
                return {
                    text: "Grouping photos…",
                    bg: "bg-gray-100 text-gray-700 border-gray-200",
                };
            case "analysis_queued":
                return {
                    text: "Analyzing item…",
                    bg: "bg-blue-50 text-blue-700 border-blue-200 animate-pulse",
                };
            case "analysis_running":
                return {
                    text: "Generating title and price…",
                    bg: "bg-blue-50 text-blue-700 border-blue-200 animate-pulse",
                };
            case "ready_for_approval":
                return {
                    text: "Ready for review",
                    bg: "bg-emerald-50 text-emerald-700 border-emerald-200",
                };
            case "needs_review":
                if (item?.listing?.analysisStatus === "failed") {
                    return {
                        text: "Analysis failed",
                        bg: "bg-rose-50 text-rose-700 border-rose-200",
                    };
                }
                if (item?.listing?.analysisStatus === "timeout") {
                    return {
                        text: "Analysis timed out",
                        bg: "bg-amber-100 text-amber-800 border-amber-300",
                    };
                }
                return {
                    text: "Needs review",
                    bg: "bg-amber-50 text-amber-700 border-amber-200",
                };
            case "approved":
                return {
                    text: "Approved",
                    bg: "bg-teal-50 text-teal-700 border-teal-200",
                };
            case "draft_submitted":
            case "draft_result_available":
                return {
                    text: "Draft submitted",
                    bg: "bg-cyan-50 text-cyan-700 border-cyan-200",
                };
            case "draft_failed":
                return {
                    text: "Draft failed",
                    bg: "bg-rose-50 text-rose-700 border-rose-200",
                };
            default:
                return {
                    text: status || "Ready for review",
                    bg: "bg-gray-100 text-gray-700 border-gray-200",
                };
        }
    }
</script>

<svelte:window
    on:keydown={(e) => e.key === "Escape" && isUploadOpen && closeUploadModal()}
/>

<div class="workspace-root">
    <!-- Header Section matching Mockup media_1792112172110.png -->
    <header class="workspace-header">
        <div class="header-titles">
            <h2 class="text-2xl font-bold tracking-tight text-slate-900">
                HHT Listing Workspace
            </h2>
            <p class="text-sm text-slate-500 font-medium">
                New inventory · Spreadsheet operational center
            </p>
        </div>

        <div class="header-right">
            <!-- Selected Count Badge -->
            {#if selectedItemIds.size > 0}
                <span class="selection-badge">
                    {selectedItemIds.size} selected
                </span>
            {/if}

            <!-- Autosave Indicator -->
            <div class="autosave-indicator">
                {#if autosaveStatus === "saving"}
                    <span
                        class="text-xs text-blue-600 font-semibold flex items-center gap-1"
                    >
                        <span
                            class="inline-block w-2 h-2 rounded-full bg-blue-600 animate-ping"
                        ></span>
                        Saving...
                    </span>
                {:else if autosaveStatus === "saved"}
                    <span
                        class="text-xs text-emerald-600 font-semibold flex items-center gap-1"
                    >
                        <svg
                            class="w-3.5 h-3.5"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                        >
                            <path
                                fill-rule="evenodd"
                                d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                                clip-rule="evenodd"
                            />
                        </svg>
                        Saved
                    </span>
                {:else if autosaveStatus === "error"}
                    <button
                        class="text-xs text-rose-600 font-bold underline"
                        on:click={flushPendingPatches}
                    >
                        Save failed (Retry)
                    </button>
                {/if}
            </div>
        </div>
    </header>

    <!-- Sub-tab Toggle: New Inventory vs Listing Optimization -->
    <div class="subtab-toggle-container">
        <div class="subtab-pill-box">
            <button
                class="subtab-pill"
                class:active={activeSubTab === "new_inventory"}
                on:click={() => (activeSubTab = "new_inventory")}
            >
                New inventory
            </button>
            <button
                class="subtab-pill"
                class:active={activeSubTab === "listing_optimization"}
                on:click={() => {
                    activeSubTab = "listing_optimization";
                    onSwitchToListingOptimization();
                }}
            >
                Listing optimization
            </button>
        </div>

        <!-- Batch Selector & Creation & Lifecycle Summary -->
        <div class="batch-selector-box">
            <label
                for="batch-select-dropdown"
                class="text-xs font-semibold text-slate-500">Batch:</label
            >
            <select
                id="batch-select-dropdown"
                class="batch-select"
                bind:value={selectedBatchId}
                on:change={() => selectBatch(selectedBatchId)}
                disabled={batchLoading}
            >
                {#each batches as b}
                    <option value={b.id}>{b.name} ({b.itemCount} items)</option>
                {/each}
            </select>
            <button
                class="batch-new-btn"
                on:click={createNewBatch}
                title="Create new batch">+ New Batch</button
            >

            <!-- Batch Actions Menu -->
            <div class="batch-actions-dropdown-container">
                <button
                    type="button"
                    class="batch-new-btn batch-actions-trigger"
                    on:click={() => (isBatchActionsMenuOpen = !isBatchActionsMenuOpen)}
                >
                    Batch actions ▾
                </button>
                {#if isBatchActionsMenuOpen}
                    <div class="batch-actions-menu">
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                regroupBatchPhotos("1");
                            }}
                        >
                            Regroup: 1 photo per item
                        </button>
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                regroupBatchPhotos("2");
                            }}
                        >
                            Regroup: 2 photos per item
                        </button>
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                regroupBatchPhotos("3");
                            }}
                        >
                            Regroup: 3 photos per item
                        </button>
                        <hr class="menu-divider" />
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                reprocessBatch();
                            }}
                        >
                            Reprocess / Re-analyze batch
                        </button>
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                onOpenAnalysisMonitor();
                            }}
                        >
                            Analysis Monitor &amp; Diagnostics ↗
                        </button>
                        <button
                            type="button"
                            class="menu-item"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                isTemplateModalOpen = true;
                            }}
                        >
                            Configure eBay Draft Template (.csv)...
                        </button>
                        <hr class="menu-divider" />
                        <button
                            type="button"
                            class="menu-item menu-danger"
                            on:click={() => {
                                isBatchActionsMenuOpen = false;
                                deleteSelectedBatch();
                            }}
                            disabled={batchLoading || !selectedBatchId || items.some(it => it.workflowStatus === 'draft_submitted' || it.workflowStatus === 'draft_result_available')}
                            title={items.some(it => it.workflowStatus === 'draft_submitted' || it.workflowStatus === 'draft_result_available') ? "Cannot delete batch after eBay draft submission" : "Delete unsubmitted batch"}
                        >
                            Delete batch
                        </button>
                    </div>
                {/if}
            </div>
        </div>
    </div>

    <!-- Batch Lifecycle Summary Bar -->
    <div class="batch-lifecycle-summary">
        <div class="lifecycle-chip">
            <span class="chip-label">Batch:</span>
            <strong class="chip-value">{selectedBatch?.name || "No batch"}</strong>
        </div>
        <div class="lifecycle-chip">
            <span class="chip-label">Total items:</span>
            <strong class="chip-value">{totalItemCount}</strong>
        </div>
        <div class="lifecycle-chip" class:highlight={analyzingCount > 0}>
            <span class="chip-label">Analyzing:</span>
            <strong class="chip-value">{analyzingCount}</strong>
        </div>
        <div class="lifecycle-chip">
            <span class="chip-label">Ready for review:</span>
            <strong class="chip-value">{readyCount}</strong>
        </div>
        <div class="lifecycle-chip" class:highlight-warn={needsReviewCount > 0}>
            <span class="chip-label">Needs review:</span>
            <strong class="chip-value">{needsReviewCount}</strong>
        </div>
        <div class="lifecycle-chip" class:highlight-success={approvedCount > 0}>
            <span class="chip-label">Approved:</span>
            <strong class="chip-value">{approvedCount}</strong>
        </div>
        <div class="lifecycle-chip">
            <span class="chip-label">Draft submitted:</span>
            <strong class="chip-value">{submittedCount}</strong>
        </div>
    </div>

    <!-- Instruction Help Bar with View Mode Toggle -->
    <div class="instruction-bar">
        <div class="flex items-center gap-3">
            <span class="text-xs text-slate-600 font-medium">
                {viewMode === "cards"
                    ? "Listing Cards · Visual review with live eBay readiness & 1-click pricing. Click any card for item specifics."
                    : "CSV Draft Verification Table · 11-column spreadsheet for verifying draft export values."}
            </span>
        </div>
        <div class="flex items-center gap-3">
            <!-- View Mode Segmented Control -->
            <div class="view-mode-toggle">
                <button
                    type="button"
                    class="view-toggle-btn"
                    class:active={viewMode === "cards"}
                    on:click={() => (viewMode = "cards")}
                    title="Visual card grid (MetaMuse style)"
                >
                    <span class="mr-1">🎴</span> Cards
                </button>
                <button
                    type="button"
                    class="view-toggle-btn"
                    class:active={viewMode === "table"}
                    on:click={() => (viewMode = "table")}
                    title="11-column spreadsheet table for CSV verification"
                >
                    <span class="mr-1">📊</span> Table
                </button>
            </div>

            <!-- Search & Filter Controls -->
            <input
                type="text"
                class="search-input"
                placeholder="Filter title, brand, category..."
                bind:value={searchQuery}
            />
            <select class="filter-select" bind:value={statusFilter}>
                <option value="all">All statuses</option>
                <option value="incomplete">Incomplete fields</option>
                <option value="needs_review">Needs Review</option>
                <option value="ready_for_approval">Ready for Approval</option>
                <option value="approved">Approved</option>
                <option value="draft_submitted">Draft Submitted</option>
            </select>
        </div>
    </div>

    <!-- Alerts / Messages -->
    {#if globalError}
        <div class="notice error mb-3">
            <p>{globalError}</p>
        </div>
    {/if}
    {#if globalStatus}
        <div class="notice info mb-3">
            <p>{globalStatus}</p>
        </div>
    {/if}

    <!-- Main Workspace Container with Cards Grid / Spreadsheet Table & Inspector -->
    <div class="spreadsheet-container">
        {#if viewMode === "cards"}
            <!-- MetaMuse-Style Listing Cards Grid -->
            <div class="cards-grid-container">
                {#if batchLoading}
                    <div class="cards-empty-state">
                        <p class="text-slate-400 font-medium animate-pulse">Loading batch items...</p>
                    </div>
                {:else if filteredItems.length === 0}
                    <div class="cards-empty-state">
                        <div class="text-4xl mb-2">📸</div>
                        <h3 class="text-base font-bold text-slate-800">No items found</h3>
                        <p class="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
                            Upload photos to automatically create, group, and analyze your inventory items.
                        </p>
                        <button
                            type="button"
                            class="action-btn-primary mt-3 inline-block"
                            on:click={openUploadModal}
                        >
                            Upload photos
                        </button>
                    </div>
                {:else}
                    <div class="cards-grid">
                        {#each filteredItems as item (item.id)}
                            {@const listing = item.listing || {}}
                            {@const readiness = getItemReadiness(item)}
                            {@const isSelected = selectedItemIds.has(item.id)}
                            {@const isInspecting = inspectingItemId === item.id}
                            {@const statusInfo = getStatusBadge(item.workflowStatus, item)}

                            <div
                                class="listing-card"
                                class:selected={isSelected}
                                class:inspecting={isInspecting}
                                class:is-approved={item.workflowStatus === 'approved'}
                            >
                                <!-- Card Header / Media Container -->
                                <div class="card-header-container">
                                    <button
                                        type="button"
                                        class="card-media"
                                        on:click={() => openItemInspector(item)}
                                        aria-label="Inspect item details and specifics"
                                    >
                                        {#if item.coverThumbnail}
                                            <img
                                                src={item.coverThumbnail}
                                                alt={listing.title || "Item"}
                                                class="card-img"
                                                loading="lazy"
                                            />
                                        {:else}
                                            <div class="card-img-placeholder">📷</div>
                                        {/if}
                                    </button>

                                    <!-- Top Overlay Badges -->
                                    <div class="card-overlay-top">
                                        <div class="card-checkbox-label">
                                            <input
                                                type="checkbox"
                                                checked={isSelected}
                                                on:change={() => toggleSelectItem(item.id)}
                                                class="row-checkbox"
                                                aria-label="Select item"
                                            />
                                        </div>

                                        <span class="card-photo-count">
                                            📷 {item.photoCount} photo{item.photoCount === 1 ? '' : 's'}
                                        </span>
                                    </div>

                                    <!-- Readiness Pill -->
                                    <div class="card-readiness-badge" class:ready={readiness.ready}>
                                        {#if item?.listing?.analysisStatus === "failed" || item?.listing?.analysisStatus === "timeout"}
                                            <span class="readiness-dot bg-rose-500"></span>
                                            <span class="text-rose-700 font-semibold">{statusInfo.text}</span>
                                        {:else if readiness.ready}
                                            <span class="readiness-dot bg-emerald-500"></span>
                                            <span>Ready for eBay Draft</span>
                                        {:else if readiness.missing.length > 0}
                                            <span class="readiness-dot bg-amber-500"></span>
                                            <span>Missing {readiness.missing[0]}</span>
                                        {:else}
                                            <span class="readiness-dot bg-blue-500"></span>
                                            <span>{statusInfo.text}</span>
                                        {/if}
                                    </div>
                                </div>

                                <!-- Card Content -->
                                <div class="card-content">
                                    <!-- Category Tag -->
                                    <div class="card-category-row">
                                        <button
                                            type="button"
                                            class="card-category-btn"
                                            title="Click to search or change eBay category"
                                            on:click={() => openItemInspector(item)}
                                        >
                                            <span class="cat-icon">🏷️</span>
                                            <span class="cat-name">
                                                {listing.categoryName || (listing.cat ? `Category #${listing.cat}` : "Select Category...")}
                                            </span>
                                        </button>
                                        {#if listing.cid === "1000" || listing.condition === "New"}
                                            <span class="cond-pill cond-new">New</span>
                                        {:else}
                                            <span class="cond-pill cond-preowned">Pre-owned</span>
                                        {/if}
                                    </div>

                                    <!-- Listing Title with Character Count -->
                                    <div class="card-title-row">
                                        <div class="flex items-center justify-between text-[11px] text-slate-400 mb-1">
                                            <span class="font-medium text-slate-500">eBay Title</span>
                                            <span
                                                class="font-mono font-bold"
                                                class:text-rose-600={(listing.title || "").length > 80}
                                                class:text-emerald-700={(listing.title || "").length > 0 && (listing.title || "").length <= 80}
                                            >
                                                {(listing.title || "").length}/80
                                            </span>
                                        </div>
                                        <input
                                            type="text"
                                            class="card-title-input"
                                            value={listing.title || ""}
                                            placeholder="Enter listing title..."
                                            maxlength="80"
                                            on:input={(e) => handleInlineEdit(item, "title", e.target.value)}
                                            on:blur={() => handleInlineEdit(item, "confirmField", "title")}
                                        />

                                        {#if item.titleCandidates && Object.keys(item.titleCandidates).length > 1}
                                            <div class="mt-1 flex items-center justify-between">
                                                <button
                                                    type="button"
                                                    class="card-cand-toggle-btn"
                                                    on:click|stopPropagation={() => {
                                                        activeTitleCandidatesItemId = activeTitleCandidatesItemId === item.id ? null : item.id;
                                                    }}
                                                >
                                                    ✨ Alternative Titles ({Object.keys(item.titleCandidates).length})
                                                </button>
                                            </div>

                                            {#if activeTitleCandidatesItemId === item.id}
                                                <div class="candidate-dropdown mt-1">
                                                    <div class="p-2 border-b text-[11px] font-bold text-slate-700">Choose Title Option</div>
                                                    {#each Object.entries(item.titleCandidates) as [cKey, cand]}
                                                        <button
                                                            type="button"
                                                            class="candidate-option-btn"
                                                            on:click={() => selectTitleCandidate(item, cKey)}
                                                        >
                                                            <div class="flex justify-between text-[11px] font-semibold text-slate-700">
                                                                <span class="capitalize">{cKey.replace("_", " ")}</span>
                                                                <span class="text-slate-400">{cand.length}/80</span>
                                                            </div>
                                                            <p class="text-xs text-slate-900 mt-0.5">{cand.title || cand}</p>
                                                        </button>
                                                    {/each}
                                                </div>
                                            {/if}
                                        {/if}
                                    </div>

                                    <!-- Specifics Chips -->
                                    <div class="card-specifics-chips">
                                        {#if listing.brand}
                                            <span class="spec-chip"><strong>Brand:</strong> {listing.brand}</span>
                                        {/if}
                                        {#if listing.size && listing.size !== "Not visible"}
                                            <span class="spec-chip"><strong>Size:</strong> {listing.size}</span>
                                        {/if}
                                        {#if listing.color}
                                            <span class="spec-chip"><strong>Color:</strong> {listing.color}</span>
                                        {/if}
                                        {#if listing.sku}
                                            <span class="spec-chip font-mono"><strong>SKU:</strong> {listing.sku}</span>
                                        {/if}
                                    </div>

                                    <!-- Price Display & Quick Tiers -->
                                    <div class="card-price-section">
                                        <div class="flex items-center justify-between mb-1.5">
                                            <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">Listing Price</span>
                                            <div class="relative flex items-center">
                                                <span class="absolute left-2 text-xs font-bold text-slate-400">$</span>
                                                <input
                                                    type="number"
                                                    step="0.01"
                                                    min="0.01"
                                                    class="card-price-input"
                                                    value={listing.price || ""}
                                                    placeholder="0.00"
                                                    on:input={(e) => handleInlineEdit(item, "price", parseFloat(e.target.value) || 0)}
                                                    on:blur={() => handleInlineEdit(item, "confirmField", "price")}
                                                />
                                            </div>
                                        </div>

                                        <!-- Fast 1-click Price Tiers -->
                                        <div class="card-price-tiers">
                                            <button
                                                type="button"
                                                class="price-tier-chip"
                                                title="Quick sale price"
                                                on:click={() => {
                                                    const p = item.quickSalePrice || item.pricingTiers?.quickSale?.price;
                                                    if (p) {
                                                        handleInlineEdit(item, "price", parseFloat(p));
                                                        handleInlineEdit(item, "confirmField", "price");
                                                    }
                                                }}
                                            >
                                                <span class="tier-lbl">Quick</span>
                                                <span class="tier-cost">${item.quickSalePrice || item.pricingTiers?.quickSale?.price || "--"}</span>
                                            </button>
                                            <button
                                                type="button"
                                                class="price-tier-chip active-tier"
                                                title="Balanced price"
                                                on:click={() => {
                                                    const p = item.balancedPrice || item.pricingTiers?.balanced?.price || listing.price;
                                                    if (p) {
                                                        handleInlineEdit(item, "price", parseFloat(p));
                                                        handleInlineEdit(item, "confirmField", "price");
                                                    }
                                                }}
                                            >
                                                <span class="tier-lbl">Balanced</span>
                                                <span class="tier-cost">${item.balancedPrice || item.pricingTiers?.balanced?.price || listing.price || "--"}</span>
                                            </button>
                                            <button
                                                type="button"
                                                class="price-tier-chip"
                                                title="Premium margin price"
                                                on:click={() => {
                                                    const p = item.premiumPrice || item.pricingTiers?.premium?.price;
                                                    if (p) {
                                                        handleInlineEdit(item, "price", parseFloat(p));
                                                        handleInlineEdit(item, "confirmField", "price");
                                                    }
                                                }}
                                            >
                                                <span class="tier-lbl">Premium</span>
                                                <span class="tier-cost">${item.premiumPrice || item.pricingTiers?.premium?.price || "--"}</span>
                                            </button>
                                        </div>
                                    </div>
                                </div>

                                <!-- Card Footer Action Buttons -->
                                <div class="card-footer">
                                    <button
                                        type="button"
                                        class="card-action-btn-secondary"
                                        on:click={() => openItemInspector(item)}
                                    >
                                        Edit Specifics ↗
                                    </button>

                                    {#if item.workflowStatus === "approved"}
                                        <span class="approved-badge">
                                            ✓ Approved
                                        </span>
                                    {:else if item.workflowStatus === "draft_submitted" || item.workflowStatus === "draft_result_available"}
                                        <span class="draft-submitted-badge">
                                            ✓ Draft Sent
                                        </span>
                                    {:else if item.listing?.analysisStatus === "failed" || item.listing?.analysisStatus === "timeout" || (item.workflowStatus === "needs_review" && item.listing?.analysisError)}
                                        <button
                                            type="button"
                                            class="card-action-btn-retry"
                                            on:click={() => analyzeWorkspaceItem(item.id)}
                                            disabled={actionLoading}
                                            title={item.listing?.analysisError || "Retry analysis"}
                                        >
                                            ↻ Retry Analysis
                                        </button>
                                    {:else}
                                        <button
                                            type="button"
                                            class="card-action-btn-approve"
                                            on:click={() => approveSingleItem(item)}
                                            disabled={actionLoading}
                                        >
                                            Approve ✓
                                        </button>
                                    {/if}
                                </div>
                            </div>
                        {/each}
                    </div>
                {/if}
            </div>
        {:else}
            <!-- Main Spreadsheet Table (11-Column CSV Verification View) -->
            <div class="table-scroll-wrapper">
                <table class="inventory-table">
                <thead>
                    <tr>
                        <th class="w-12 text-center">
                            <input
                                type="checkbox"
                                checked={selectedItemIds.size ===
                                    filteredItems.length &&
                                    filteredItems.length > 0}
                                on:change={toggleSelectAll}
                                class="row-checkbox"
                            />
                        </th>
                        <th class="w-24">Item</th>
                        <th
                            class="min-w-[280px]"
                            on:click={() => toggleSort("title")}
                        >
                            Title {sortBy === "title"
                                ? sortDirection === "asc"
                                    ? "↑"
                                    : "↓"
                                : ""}
                        </th>
                        <th class="min-w-[150px]">Category</th>
                        <th class="w-32">Brand</th>
                        <th class="w-28">Size</th>
                        <th class="w-28">Condition</th>
                        <th class="w-32" on:click={() => toggleSort("price")}>
                            Price {sortBy === "price"
                                ? sortDirection === "asc"
                                    ? "↑"
                                    : "↓"
                                : ""}
                        </th>
                        <th class="w-20">Qty</th>
                        <th class="w-32" on:click={() => toggleSort("status")}>
                            Status {sortBy === "status"
                                ? sortDirection === "asc"
                                    ? "↑"
                                    : "↓"
                                : ""}
                        </th>
                        <th class="w-28">Updated</th>
                    </tr>
                </thead>
                <tbody>
                    {#if batchLoading}
                        <tr>
                            <td
                                colspan="11"
                                class="text-center py-12 text-slate-400"
                                >Loading batch items...</td
                            >
                        </tr>
                    {:else if filteredItems.length === 0}
                        <tr>
                            <td
                                colspan="11"
                                class="text-center py-16 text-slate-500"
                            >
                                <p class="text-base font-semibold mb-1">
                                    No items found in this view.
                                </p>
                                <p class="text-sm text-slate-400">
                                    Upload photos to create and group items, or
                                    change filters.
                                </p>
                                <button
                                    class="mt-4 px-4 py-2 bg-slate-900 text-white rounded-lg text-sm font-semibold hover:bg-slate-800"
                                    on:click={openUploadModal}
                                >
                                    Upload photos
                                </button>
                            </td>
                        </tr>
                    {:else}
                        {#each filteredItems as item (item.id)}
                            {@const listing = item.listing || {}}
                            {@const prov = item.provenance || {}}
                            {@const statusInfo = getStatusBadge(
                                item.workflowStatus,
                                item,
                            )}
                            {@const isMissingSize =
                                !listing.size || listing.size === "Not visible"}
                            {@const isSelected = selectedItemIds.has(item.id)}
                            {@const isInspecting = inspectingItemId === item.id}

                            <tr
                                class:selected-row={isSelected}
                                class:inspecting-row={isInspecting}
                            >
                                <!-- Checkbox -->
                                <td
                                    class="text-center"
                                    on:click|stopPropagation
                                >
                                    <input
                                        type="checkbox"
                                        checked={isSelected}
                                        on:change={() =>
                                            toggleSelectItem(item.id)}
                                        class="row-checkbox"
                                    />
                                </td>

                                <!-- Thumbnail & Photo Count Pill matching media_1792112229.png -->
                                <td
                                    class="cursor-pointer"
                                    on:click={() => openItemInspector(item)}
                                >
                                    <div class="thumb-cell">
                                        <div class="thumb-box">
                                            {#if item.coverThumbnail}
                                                <img
                                                    src={item.coverThumbnail}
                                                    alt={listing.title ||
                                                        "Item"}
                                                    class="thumb-img"
                                                />
                                            {:else}
                                                <div class="thumb-placeholder">
                                                    📷
                                                </div>
                                            {/if}
                                        </div>
                                        <span class="photo-count-label">
                                            {item.photoCount} photo{item.photoCount ===
                                            1
                                                ? ""
                                                : "s"}
                                        </span>
                                    </div>
                                </td>

                                <!-- Title (with Candidate Picker popover) -->
                                <td>
                                    <div class="relative">
                                        <input
                                            type="text"
                                            class="table-input"
                                            value={listing.title || ""}
                                            placeholder="Enter listing title..."
                                            maxlength="80"
                                            on:input={(e) =>
                                                handleInlineEdit(
                                                    item,
                                                    "title",
                                                    e.target.value,
                                                )}
                                        />
                                        {#if item.titleCandidates && Object.keys(item.titleCandidates).length > 0}
                                            <button
                                                type="button"
                                                class="candidate-toggle-btn"
                                                title="Select optimized title candidate"
                                                on:click|stopPropagation={() => {
                                                    activeTitleCandidatesItemId =
                                                        activeTitleCandidatesItemId ===
                                                        item.id
                                                            ? null
                                                            : item.id;
                                                }}
                                            >
                                                ✨
                                            </button>
                                            {#if activeTitleCandidatesItemId === item.id}
                                                <div class="candidate-dropdown">
                                                    <div
                                                        class="p-2 border-b text-xs font-bold text-slate-700"
                                                    >
                                                        Select Title Candidate
                                                    </div>
                                                    {#each Object.entries(item.titleCandidates) as [cKey, cand]}
                                                        <button
                                                            type="button"
                                                            class="candidate-option-btn"
                                                            on:click={() =>
                                                                selectTitleCandidate(
                                                                    item,
                                                                    cKey,
                                                                )}
                                                        >
                                                            <div
                                                                class="flex justify-between text-xs font-semibold"
                                                            >
                                                                <span
                                                                    class="capitalize"
                                                                    >{cKey.replace(
                                                                        "_",
                                                                        " ",
                                                                    )}</span
                                                                >
                                                                <span
                                                                    class="text-slate-400"
                                                                    >{cand.length}/80</span
                                                                >
                                                            </div>
                                                            <p
                                                                class="text-xs text-slate-800 mt-0.5"
                                                            >
                                                                {cand.title}
                                                            </p>
                                                        </button>
                                                    {/each}
                                                </div>
                                            {/if}
                                        {/if}
                                    </div>
                                </td>

                                <!-- Category -->
                                <td>
                                    <div class="flex flex-col gap-1">
                                        <input
                                            type="text"
                                            class="table-input"
                                            value={listing.categoryName || listing.cat || ""}
                                            placeholder="Choose eBay category"
                                            readonly
                                            on:click={() => openItemInspector(item)}
                                        />
                                        <button
                                            type="button"
                                            class="text-[10px] text-blue-700 font-semibold text-left hover:underline"
                                            on:click={() => openItemInspector(item)}
                                        >
                                            {listing.cat ? "Edit eBay specifics" : "Search eBay category"}
                                        </button>
                                    </div>
                                </td>

                                <!-- Brand -->
                                <td>
                                    <input
                                        type="text"
                                        class="table-input"
                                        value={listing.brand || ""}
                                        placeholder="Brand..."
                                        on:input={(e) =>
                                            handleInlineEdit(
                                                item,
                                                "brand",
                                                e.target.value,
                                            )}
                                    />
                                </td>

                                <!-- Size -->
                                <td>
                                    <div class="relative">
                                        <input
                                            type="text"
                                            class="table-input"
                                            class:missing-input={isMissingSize}
                                            value={listing.size || ""}
                                            placeholder="Set size"
                                            on:input={(e) =>
                                                handleInlineEdit(
                                                    item,
                                                    "size",
                                                    e.target.value,
                                                )}
                                        />
                                    </div>
                                </td>

                                <!-- Condition -->
                                <td>
                                    <span
                                        class="text-sm text-slate-700 font-medium"
                                    >
                                        {listing.cid === "1000"
                                            ? "New"
                                            : "Pre-owned"}
                                    </span>
                                </td>

                                <!-- Price (with Tier options popover and AI Estimate notice) -->
                                <td>
                                    <div class="relative flex flex-col gap-0.5">
                                        <div class="flex items-center gap-1">
                                            <span class="text-slate-400 text-sm">$</span>
                                            <input
                                                type="number"
                                                step="0.01"
                                                class="table-input w-20 text-right font-semibold"
                                                value={listing.price || ""}
                                                placeholder="0.00"
                                                on:input={(e) => {
                                                    handleInlineEdit(item, "price", e.target.value);
                                                    handleInlineEdit(item, "confirmField", "price");
                                                }}
                                            />
                                            {#if item.pricingOptions && Object.keys(item.pricingOptions).length > 0}
                                                <button
                                                    type="button"
                                                    class="price-tiers-btn"
                                                    title="View recommended price tiers"
                                                    on:click|stopPropagation={() => {
                                                        activePriceTiersItemId =
                                                            activePriceTiersItemId === item.id ? null : item.id;
                                                    }}
                                                >
                                                    🏷️
                                                </button>
                                            {/if}
                                        </div>
                                        <div class="text-[10px] text-slate-500 flex items-center gap-1 whitespace-nowrap">
                                            {#if item.provenance?.price?.sellerConfirmed || item.sellerConfirmed}
                                                <span class="text-emerald-700 font-bold">✓ Confirmed</span>
                                            {:else}
                                                <span class="text-amber-700 font-medium" title="AI estimate — verify before approval">AI estimate</span>
                                            {/if}
                                            {#if item.pricingOptions?.quickSale?.price}
                                                <span class="text-slate-400" title="Quick Sale price">· Q:${item.pricingOptions.quickSale.price}</span>
                                            {/if}
                                            {#if item.pricingOptions?.balanced?.price}
                                                <span class="text-slate-400" title="Balanced price">· B:${item.pricingOptions.balanced.price}</span>
                                            {/if}
                                        </div>
                                        {#if activePriceTiersItemId === item.id}
                                            <div class="candidate-dropdown">
                                                <div class="p-2 border-b text-xs font-bold text-slate-700 flex justify-between items-center">
                                                    <span>Recommended Price Tiers</span>
                                                    <span class="text-[10px] text-amber-700 font-normal">AI estimate — verify before approval</span>
                                                </div>
                                                {#each Object.entries(item.pricingOptions) as [tKey, opt]}
                                                    <button
                                                        type="button"
                                                        class="candidate-option-btn"
                                                        on:click={() => selectPricingOption(item, tKey)}
                                                    >
                                                        <div class="flex justify-between items-center text-xs font-bold">
                                                            <span>{opt.label}</span>
                                                            <span class="text-emerald-700">${opt.price}</span>
                                                        </div>
                                                        <p class="text-xs text-slate-500 mt-0.5">
                                                            {opt.reason}
                                                        </p>
                                                    </button>
                                                {/each}
                                            </div>
                                        {/if}
                                    </div>
                                </td>

                                <!-- Quantity -->
                                <td>
                                    <input
                                        type="number"
                                        class="table-input w-16 text-center"
                                        value={listing.quantity || 1}
                                        min="1"
                                        on:input={(e) =>
                                            handleInlineEdit(
                                                item,
                                                "quantity",
                                                Number(e.target.value) || 1,
                                            )}
                                    />
                                </td>

                                <!-- Status Badge matching media_1792112229.png -->
                                <td>
                                    {#if item.listing?.analysisStatus === "failed" || item.listing?.analysisStatus === "timeout" || (item.workflowStatus === "needs_review" && item.listing?.analysisError)}
                                        <div class="flex items-center gap-1.5 flex-wrap">
                                            <span class="status-pill {statusInfo.bg}" title={item.listing?.analysisError || ""}>
                                                {statusInfo.text}
                                            </span>
                                            <button
                                                type="button"
                                                class="text-[11px] font-semibold text-rose-600 hover:text-rose-800 underline"
                                                on:click|stopPropagation={() => analyzeWorkspaceItem(item.id)}
                                                disabled={actionLoading}
                                            >
                                                Retry
                                            </button>
                                        </div>
                                    {:else if isMissingSize && item.workflowStatus === "needs_review"}
                                        <span
                                            class="status-pill bg-amber-50 text-amber-700 border-amber-200"
                                        >
                                            Missing size
                                        </span>
                                    {:else}
                                        <span
                                            class="status-pill {statusInfo.bg}"
                                        >
                                            {statusInfo.text}
                                        </span>
                                    {/if}
                                </td>

                                <!-- Updated -->
                                <td
                                    class="text-xs text-slate-500 whitespace-nowrap"
                                >
                                    {formatTime(item.updatedAt)}
                                </td>
                            </tr>
                        {/each}
                    {/if}
                </tbody>
            </table>
        </div>
        {/if}

        <!-- Slide-Over Item Inspector Panel -->
        {#if inspectingItem}
            {@const inspReadiness = getItemReadiness(inspectingItem)}
            <aside class="inspector-drawer">
                <div class="inspector-header">
                    <div>
                        <h3 class="text-base font-bold text-slate-900">
                            Item Inspector
                        </h3>
                        <p class="text-xs text-slate-500">
                            {inspectingItem.id.slice(0, 8)}
                        </p>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <button
                            type="button"
                            class="full-review-btn"
                            title="Open in full-page Review form"
                            on:click={() => onOpenFullReview(inspectingItem)}
                        >
                            Full Review ↗
                        </button>
                        <button class="close-btn" on:click={closeItemInspector}
                            >✕</button
                        >
                    </div>
                </div>

                <div class="inspector-body">
                    <!-- eBay Listing Readiness Health Check Banner -->
                    <div class="inspector-readiness-banner" class:is-ready={inspReadiness.ready}>
                        <div class="flex items-center justify-between mb-2">
                            <div class="flex items-center gap-2">
                                <span class="text-sm font-bold {inspReadiness.ready ? 'text-emerald-900' : 'text-slate-800'}">
                                    {inspReadiness.ready ? "✓ Ready for eBay Draft" : "eBay Listing Readiness"}
                                </span>
                                <span class="text-[11px] font-bold px-2 py-0.5 rounded-full {inspReadiness.ready ? 'bg-emerald-200 text-emerald-800' : 'bg-amber-100 text-amber-800'}">
                                    {inspReadiness.percent}%
                                </span>
                            </div>
                            {#if !inspReadiness.ready && inspectingItem.workflowStatus !== 'approved'}
                                <span class="text-[10px] text-amber-700 font-medium">
                                    {inspReadiness.missing.length} item(s) needed
                                </span>
                            {:else if inspectingItem.workflowStatus === 'approved'}
                                <span class="text-[10px] bg-teal-100 text-teal-800 font-bold px-2 py-0.5 rounded">
                                    Approved
                                </span>
                            {/if}
                        </div>
                        <div class="grid grid-cols-3 gap-1.5 text-[11px]">
                            <div class="readiness-check-item" class:passed={inspectingItem.photos?.length > 0}>
                                <span class="indicator">{inspectingItem.photos?.length > 0 ? "✓" : "○"}</span>
                                <span>Photos ({inspectingItem.photoCount || inspectingItem.photos?.length || 0})</span>
                            </div>
                            <div class="readiness-check-item" class:passed={Boolean(inspectingItem.listing?.title)}>
                                <span class="indicator">{inspectingItem.listing?.title ? "✓" : "○"}</span>
                                <span>Title</span>
                            </div>
                            <div class="readiness-check-item" class:passed={Boolean(inspectingItem.listing?.price)}>
                                <span class="indicator">{inspectingItem.listing?.price ? "✓" : "○"}</span>
                                <span>Price</span>
                            </div>
                            <div class="readiness-check-item" class:passed={Boolean(inspectingItem.listing?.cat)}>
                                <span class="indicator">{inspectingItem.listing?.cat ? "✓" : "○"}</span>
                                <span>Category</span>
                            </div>
                            <div class="readiness-check-item" class:passed={Boolean(inspectingItem.listing?.cid || inspectingItem.listing?.condition)}>
                                <span class="indicator">{inspectingItem.listing?.cid || inspectingItem.listing?.condition ? "✓" : "○"}</span>
                                <span>Condition</span>
                            </div>
                            <div class="readiness-check-item" class:passed={!inspectingItem.rulesCheck?.missingRequiredAspects?.length}>
                                <span class="indicator">{!inspectingItem.rulesCheck?.missingRequiredAspects?.length ? "✓" : "○"}</span>
                                <span>Aspects</span>
                            </div>
                        </div>
                    </div>
                    <!-- Photo Gallery for Item -->
                    <div class="inspector-section">
                        <h4 class="inspector-section-title">
                            Photos ({inspectingItem.photoCount})
                        </h4>
                        <div class="inspector-photo-grid">
                            {#each inspectingItem.photos || [] as photo, pIdx}
                                <div
                                    class="inspector-photo-card"
                                    class:is-cover={photo.isCover}
                                >
                                    <img
                                        src={photo.previewUrl ||
                                            photo.storageUrl}
                                        alt={photo.originalFilename}
                                        class="inspector-img"
                                    />
                                    {#if photo.isCover}
                                        <span class="cover-tag">Cover</span>
                                    {/if}
                                    <span class="photo-pos">{pIdx + 1}</span>
                                </div>
                            {/each}
                        </div>
                    </div>

                    <div class="inspector-section">
                        {#if inspectingItem.listing?.analysisStatus === "failed" || inspectingItem.listing?.analysisStatus === "timeout" || (inspectingItem.workflowStatus === "needs_review" && inspectingItem.listing?.analysisError)}
                            <div class="mb-3 p-3 rounded-lg border border-rose-200 bg-rose-50 text-rose-800 text-xs">
                                <div class="font-bold text-rose-900 mb-1 flex items-center gap-1.5">
                                    <span>⚠️ Analysis {inspectingItem.listing?.analysisStatus === "timeout" ? "Timed Out" : "Failed"}</span>
                                </div>
                                <p class="mb-2 leading-relaxed">
                                    {inspectingItem.listing?.analysisError || "Analysis could not complete. Please retry shortly."}
                                </p>
                                <button
                                    type="button"
                                    class="px-3 py-1.5 text-xs font-bold rounded bg-rose-600 text-white hover:bg-rose-700 transition"
                                    on:click={() => analyzeWorkspaceItem(inspectingItem.id)}
                                    disabled={actionLoading || !inspectingItem.photos?.length}
                                >
                                    ↻ Retry Analysis
                                </button>
                            </div>
                        {/if}
                        <button
                            type="button"
                            class="action-btn-primary w-full"
                            on:click={() => analyzeWorkspaceItem(inspectingItem.id)}
                            disabled={actionLoading || !inspectingItem.photos?.length}
                        >
                            {actionLoading
                                ? "Queueing photo analysis..."
                                : (inspectingItem.listing?.analysisStatus === "failed" || inspectingItem.listing?.analysisStatus === "timeout"
                                    ? "Retry photo analysis & price"
                                    : "Analyze photos & estimate price")}
                        </button>
                        <p class="text-xs text-slate-500 mt-2">
                            Runs the existing vision worker from Workspace. No Analyze-tab upload is required.
                        </p>
                    </div>

                    <!-- Title & SKU Section -->
                    <div class="inspector-section">
                        <div class="flex items-center justify-between mb-1">
                            <h4 class="inspector-section-title mb-0">Title & SKU</h4>
                            <span class="text-xs font-semibold" class:text-rose-600={(inspectingItem.listing?.title || "").length > 80} class:text-slate-500={(inspectingItem.listing?.title || "").length <= 80}>
                                {(inspectingItem.listing?.title || "").length}/80 chars
                            </span>
                        </div>
                        <input
                            type="text"
                            class="w-full text-xs p-2 border rounded-md text-slate-800 font-medium mb-1"
                            placeholder="Enter eBay listing title (1-80 characters)..."
                            value={inspectingItem.listing?.title || ""}
                            maxlength="80"
                            on:input={(e) => {
                                handleInlineEdit(inspectingItem, "title", e.target.value);
                                handleInlineEdit(inspectingItem, "confirmField", "title");
                            }}
                        />
                        {#if (inspectingItem.titleCandidates || []).length > 1}
                            <div class="mt-1">
                                <span class="text-[11px] text-slate-500 font-medium">Alternative suggested titles:</span>
                                <div class="flex flex-col gap-1 mt-1">
                                    {#each inspectingItem.titleCandidates as cand}
                                        <button
                                            type="button"
                                            class="text-left text-xs p-1.5 border rounded hover:bg-slate-50 text-slate-700 transition"
                                            on:click={() => {
                                                handleInlineEdit(inspectingItem, "title", cand);
                                                handleInlineEdit(inspectingItem, "confirmField", "title");
                                            }}
                                        >
                                            {cand}
                                        </button>
                                    {/each}
                                </div>
                            </div>
                        {/if}
                        <div class="mt-2">
                            <label class="block text-xs font-semibold text-slate-600 mb-0.5">
                                Custom Label (SKU)
                                <input
                                    type="text"
                                    class="w-full text-xs p-1.5 border rounded text-slate-800 mt-0.5"
                                    placeholder="e.g. HHT-2026-001"
                                    value={inspectingItem.listing?.sku || inspectingItem.sku || ""}
                                    on:input={(e) => {
                                        handleInlineEdit(inspectingItem, "sku", e.target.value);
                                    }}
                                />
                            </label>
                        </div>
                    </div>

                    <!-- Pricing & Visible Price Tiers (Requirement 8) -->
                    <div class="inspector-section">
                        <div class="flex items-center justify-between mb-1">
                            <h4 class="inspector-section-title mb-0">Pricing & Tiers</h4>
                            {#if inspectingItem.sellerConfirmed || inspectingItem.provenance?.price?.sellerConfirmed}
                                <span class="text-[11px] px-2 py-0.5 rounded font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300">
                                    ✓ Confirmed
                                </span>
                            {:else}
                                <span class="text-[11px] px-2 py-0.5 rounded font-semibold bg-amber-100 text-amber-800 border border-amber-300">
                                    AI estimate — verify before approval
                                </span>
                            {/if}
                        </div>
                        <div class="flex items-center gap-2 mb-2">
                            <div class="relative flex-1">
                                <span class="absolute left-2.5 top-2 text-slate-400 text-sm">$</span>
                                <input
                                    type="number"
                                    step="0.01"
                                    min="0.01"
                                    class="w-full text-sm pl-6 pr-2 py-1.5 border rounded-md font-bold text-slate-900"
                                    placeholder="0.00"
                                    value={inspectingItem.listing?.price || ""}
                                    on:input={(e) => {
                                        handleInlineEdit(inspectingItem, "price", parseFloat(e.target.value) || 0);
                                        handleInlineEdit(inspectingItem, "confirmField", "price");
                                    }}
                                />
                            </div>
                            <div class="w-24">
                                <label class="text-[10px] uppercase font-bold text-slate-500 block">
                                    Quantity
                                    <input
                                        type="number"
                                        min="1"
                                        class="w-full text-xs p-1.5 border rounded text-slate-800 mt-0.5"
                                        value={inspectingItem.listing?.quantity || 1}
                                        on:input={(e) => handleInlineEdit(inspectingItem, "quantity", parseInt(e.target.value, 10) || 1)}
                                    />
                                </label>
                            </div>
                        </div>
                        <div class="price-tiers-inspector-grid">
                            <button
                                type="button"
                                class="price-tier-btn"
                                title="Fast inventory turnover"
                                on:click={() => {
                                    const p = inspectingItem.quickSalePrice || inspectingItem.pricingTiers?.quickSale?.price || inspectingItem.listing?.price;
                                    if (p) {
                                        handleInlineEdit(inspectingItem, "price", parseFloat(p));
                                        handleInlineEdit(inspectingItem, "confirmField", "price");
                                    }
                                }}
                            >
                                <span class="tier-name">Quick Sale</span>
                                <span class="tier-val">${inspectingItem.quickSalePrice || inspectingItem.pricingTiers?.quickSale?.price || "--"}</span>
                            </button>
                            <button
                                type="button"
                                class="price-tier-btn"
                                title="Balanced profit and velocity"
                                on:click={() => {
                                    const p = inspectingItem.balancedPrice || inspectingItem.pricingTiers?.balanced?.price || inspectingItem.listing?.price;
                                    if (p) {
                                        handleInlineEdit(inspectingItem, "price", parseFloat(p));
                                        handleInlineEdit(inspectingItem, "confirmField", "price");
                                    }
                                }}
                            >
                                <span class="tier-name">Balanced</span>
                                <span class="tier-val">${inspectingItem.balancedPrice || inspectingItem.pricingTiers?.balanced?.price || "--"}</span>
                            </button>
                            <button
                                type="button"
                                class="price-tier-btn"
                                title="Maximum margin"
                                on:click={() => {
                                    const p = inspectingItem.premiumPrice || inspectingItem.pricingTiers?.premium?.price || inspectingItem.listing?.price;
                                    if (p) {
                                        handleInlineEdit(inspectingItem, "price", parseFloat(p));
                                        handleInlineEdit(inspectingItem, "confirmField", "price");
                                    }
                                }}
                            >
                                <span class="tier-name">Premium</span>
                                <span class="tier-val">${inspectingItem.premiumPrice || inspectingItem.pricingTiers?.premium?.price || "--"}</span>
                            </button>
                        </div>
                    </div>

                    <!-- Seller-confirmed condition -->
                    <div class="inspector-section">
                        <div class="flex items-center justify-between mb-1">
                            <h4 class="inspector-section-title mb-0">Condition</h4>
                            <span class="text-[11px] text-slate-500">eBay listing condition</span>
                        </div>
                        <select
                            class="w-full text-sm p-2 border rounded-md bg-white text-slate-800"
                            value={inspectingItem.listing?.cid || inspectingItem.listing?.cid_condition || "3000"}
                            on:change={(e) => setCondition(inspectingItem, e.target.value)}
                        >
                            <option value="1000">New</option>
                            <option value="3000">Pre-owned</option>
                        </select>
                        <p class="text-[11px] text-slate-500 mt-1">Choose the value that matches the item before approval.</p>
                    </div>
                    <!-- eBay Category & Live Aspects (Critical Requirement) -->
                    <div class="inspector-section">
                        <div class="flex items-center justify-between mb-1">
                            <h4 class="inspector-section-title mb-0">eBay Category</h4>
                            {#if inspectingItem.listing?.cat}
                                <span class="text-xs font-mono bg-slate-100 text-slate-700 px-2 py-0.5 rounded border">
                                    ID: {inspectingItem.listing.cat}
                                </span>
                            {/if}
                        </div>
                        <div class="text-xs text-slate-600 mb-2 font-medium">
                            {inspectingItem.listing?.categoryName || "(No category selected)"}
                        </div>
                        <div class="relative mb-2">
                            <input
                                type="text"
                                class="w-full text-xs p-2 border rounded text-slate-800"
                                placeholder="Search eBay category (e.g. vintage jacket, boots)..."
                                bind:value={categoryQuery}
                                on:input={(e) => searchCategories(e.target.value)}
                            />
                            {#if categorySearching}
                                <span class="absolute right-2 top-2 text-[11px] text-slate-400">Searching...</span>
                            {/if}
                            {#if categorySuggestions.length > 0}
                                <div class="category-suggestions-dropdown">
                                    {#each categorySuggestions as sug}
                                        <button
                                            type="button"
                                            class="sug-item"
                                            on:click={() => selectCategorySuggestion(inspectingItem, sug)}
                                        >
                                            <span class="sug-path">{sug.path || sug.categoryName}</span>
                                            <span class="sug-id">#{sug.categoryId}</span>
                                        </button>
                                    {/each}
                                </div>
                            {/if}
                        </div>

                        <!-- Live Category Aspects -->
                        <div class="category-aspects-container mt-3">
                            <div class="flex items-center justify-between mb-2">
                                <span class="text-xs font-bold uppercase text-slate-700">eBay Item Specifics</span>
                                {#if categoryAspectsLoading}
                                    <span class="text-[11px] text-blue-600 animate-pulse">Loading live options...</span>
                                {/if}
                            </div>

                            {#if categoryAspectsError}
                                <div class="p-2.5 bg-amber-50 border border-amber-200 rounded-md text-xs text-amber-900 mb-2 flex items-center justify-between">
                                    <span>{categoryAspectsError}</span>
                                    <button
                                        type="button"
                                        class="px-2 py-1 bg-amber-200 hover:bg-amber-300 rounded font-semibold text-[11px]"
                                        on:click={() => loadCategoryAspects(inspectingItem.listing?.cat)}
                                    >
                                        Retry
                                    </button>
                                </div>
                            {/if}

                            {#if categoryAspects.length > 0}
                                {@const reqAspects = categoryAspects.filter((a) => a.required)}
                                {@const recAspects = categoryAspects.filter((a) => a.recommended && !a.required)}

                                {#if reqAspects.length > 0}
                                    <div class="aspect-group mb-3">
                                        <span class="text-[11px] font-bold text-rose-700 uppercase tracking-wide block mb-1.5">
                                            Required by eBay ({reqAspects.length})
                                        </span>
                                        <div class="space-y-2">
                                            {#each reqAspects as aspect}
                                                {@const aVal = getAspectValue(inspectingItem, aspect.name)}
                                                {@const aProv = getAspectProvenance(inspectingItem, aspect)}
                                                <div class="aspect-field-card border-rose-200 bg-rose-50/20">
                                                    <div class="flex items-center justify-between mb-1">
                                                        <span class="text-xs font-bold text-slate-800">{aspect.name} *</span>
                                                        <span class="text-[10px] px-1.5 py-0.5 rounded border {aProv.color}">{aProv.label}</span>
                                                    </div>
                                                    {#if aspect.values && aspect.values.length > 0}
                                                        {#if aspect.multiSelect}
                                                            <div class="flex flex-wrap gap-1 mt-1">
                                                                {#each aspect.values as optVal}
                                                                    {@const isSelected = Array.isArray(aVal) ? aVal.includes(optVal) : String(aVal || "").split("|").map(s => s.trim()).includes(optVal)}
                                                                    <button
                                                                        type="button"
                                                                        class="aspect-val-pill"
                                                                        class:selected={isSelected}
                                                                        on:click={() => toggleMultiAspectValue(inspectingItem, aspect.name, optVal)}
                                                                    >
                                                                        {optVal}
                                                                    </button>
                                                                {/each}
                                                            </div>
                                                        {:else}
                                                            {#if aspect.values && aspect.values.length > 0}
                                                                <div class="flex flex-wrap gap-1 mb-1.5">
                                                                    {#each aspect.values.slice(0, 5) as optVal}
                                                                        <button
                                                                            type="button"
                                                                            class="aspect-val-pill text-[10px] py-0.5 px-2"
                                                                            class:selected={aVal === optVal}
                                                                            on:click={() => setAspectValue(inspectingItem, aspect.name, optVal)}
                                                                        >
                                                                            {optVal}
                                                                        </button>
                                                                    {/each}
                                                                </div>
                                                            {/if}
                                                            <input
                                                                class="w-full text-xs p-1.5 border rounded bg-white"
                                                                list={aspectListId(aspect.name)}
                                                                value={aVal}
                                                                placeholder={`Search or select ${aspect.name}`}
                                                                on:input={(e) => setAspectValue(inspectingItem, aspect.name, e.target.value)}
                                                            />
                                                            <datalist id={aspectListId(aspect.name)}>
                                                                {#each aspect.values as optVal}
                                                                    <option value={optVal}>{optVal}</option>
                                                                {/each}
                                                            </datalist>
                                                        {/if}
                                                    {:else}
                                                        <input
                                                            type="text"
                                                            class="w-full text-xs p-1.5 border rounded bg-white"
                                                            placeholder={`Enter ${aspect.name}...`}
                                                            value={aVal}
                                                            on:input={(e) => setAspectValue(inspectingItem, aspect.name, e.target.value)}
                                                        />
                                                    {/if}
                                                </div>
                                            {/each}
                                        </div>
                                    </div>
                                {/if}

                                {#if recAspects.length > 0}
                                    <div class="aspect-group mb-3">
                                        <span class="text-[11px] font-bold text-slate-600 uppercase tracking-wide block mb-1.5">
                                            Recommended Specifics ({recAspects.length})
                                        </span>
                                        <div class="space-y-2">
                                            {#each recAspects.slice(0, 10) as aspect}
                                                {@const aVal = getAspectValue(inspectingItem, aspect.name)}
                                                {@const aProv = getAspectProvenance(inspectingItem, aspect)}
                                                <div class="aspect-field-card">
                                                    <div class="flex items-center justify-between mb-1">
                                                        <span class="text-xs font-semibold text-slate-700">{aspect.name}</span>
                                                        <span class="text-[10px] px-1.5 py-0.5 rounded border {aProv.color}">{aProv.label}</span>
                                                    </div>
                                                    {#if aspect.values && aspect.values.length > 0}
                                                        {#if aspect.multiSelect}
                                                            <div class="flex flex-wrap gap-1 mt-1">
                                                                {#each aspect.values.slice(0, 12) as optVal}
                                                                    {@const isSelected = Array.isArray(aVal) ? aVal.includes(optVal) : String(aVal || "").split("|").map(s => s.trim()).includes(optVal)}
                                                                    <button
                                                                        type="button"
                                                                        class="aspect-val-pill"
                                                                        class:selected={isSelected}
                                                                        on:click={() => toggleMultiAspectValue(inspectingItem, aspect.name, optVal)}
                                                                    >
                                                                        {optVal}
                                                                    </button>
                                                                {/each}
                                                            </div>
                                                        {:else}
                                                            {#if aspect.values && aspect.values.length > 0}
                                                                <div class="flex flex-wrap gap-1 mb-1.5">
                                                                    {#each aspect.values.slice(0, 5) as optVal}
                                                                        <button
                                                                            type="button"
                                                                            class="aspect-val-pill text-[10px] py-0.5 px-2"
                                                                            class:selected={aVal === optVal}
                                                                            on:click={() => setAspectValue(inspectingItem, aspect.name, optVal)}
                                                                        >
                                                                            {optVal}
                                                                        </button>
                                                                    {/each}
                                                                </div>
                                                            {/if}
                                                            <input
                                                                class="w-full text-xs p-1.5 border rounded bg-white"
                                                                list={aspectListId(aspect.name)}
                                                                value={aVal}
                                                                placeholder={`Search or select ${aspect.name}`}
                                                                on:input={(e) => setAspectValue(inspectingItem, aspect.name, e.target.value)}
                                                            />
                                                            <datalist id={aspectListId(aspect.name)}>
                                                                {#each aspect.values as optVal}
                                                                    <option value={optVal}>{optVal}</option>
                                                                {/each}
                                                            </datalist>
                                                        {/if}
                                                    {:else}
                                                        <input
                                                            type="text"
                                                            class="w-full text-xs p-1.5 border rounded bg-white"
                                                            placeholder={`Enter ${aspect.name}...`}
                                                            value={aVal}
                                                            on:input={(e) => setAspectValue(inspectingItem, aspect.name, e.target.value)}
                                                        />
                                                    {/if}
                                                </div>
                                            {/each}
                                        </div>
                                    </div>
                                {/if}
                            {/if}
                        </div>
                    </div>

                    <!-- Seller research clues -->
                    <div class="inspector-section">
                        <h4 class="inspector-section-title">Research clues</h4>
                        <p class="text-xs text-slate-500 mb-3">
                            Paste factual clues from a comparable listing or
                            label. HHT saves them as seller input and generates
                            suggestions; verify every field before approval.
                        </p>
                        <textarea
                            class="w-full text-xs p-2 border rounded-md h-20 text-slate-700 mb-2"
                            placeholder="Paste an observed title or product details..."
                            bind:value={researchClues.observedTitle}
                        ></textarea>
                        <div class="form-grid compact">
                            <label class="field"
                                ><span>Brand</span><input
                                    bind:value={researchClues.brand}
                                    placeholder="e.g. Nike"
                                /></label
                            >
                            <label class="field"
                                ><span>Item type</span><input
                                    bind:value={researchClues.itemType}
                                    placeholder="e.g. football jersey"
                                /></label
                            >
                            <label class="field"
                                ><span>Model / number</span><input
                                    bind:value={researchClues.model}
                                    placeholder="e.g. #12"
                                /></label
                            >
                            <label class="field"
                                ><span>Category</span><input
                                    bind:value={researchClues.category}
                                    placeholder="e.g. sports jersey"
                                /></label
                            >
                            <label class="field"
                                ><span>Size</span><input
                                    bind:value={researchClues.size}
                                    placeholder="e.g. Men's Large"
                                /></label
                            >
                            <label class="field"
                                ><span>Comparable price</span><input
                                    type="text"
                                    bind:value={researchClues.comparablePrice}
                                    placeholder="e.g. 54.99"
                                /></label
                            >
                        </div>
                        <button
                            type="button"
                            class="action-btn-primary mt-3"
                            on:click={generateResearchSuggestions}
                            disabled={researchLoading}
                        >
                            {researchLoading
                                ? "Generating..."
                                : "Generate title & price options"}
                        </button>
                    </div>

                    <!-- Provenance Breakdown for Key Fields -->
                    <div class="inspector-section">
                        <div class="flex items-center justify-between mb-2">
                            <h4 class="inspector-section-title mb-0">
                                Field Provenance & Truth
                            </h4>
                            <button
                                class="text-xs text-blue-600 font-bold hover:underline"
                                on:click={() =>
                                    confirmAllFields(inspectingItem)}
                            >
                                Confirm All
                            </button>
                        </div>
                        <p class="text-xs text-slate-500 mb-3">
                            Verified photo evidence vs estimates. Unconfirmed
                            estimates require seller verification before
                            approval.
                        </p>

                        <div class="space-y-3">
                            {#each ["title", "price", "brand", "size", "cat", "cnote"] as fKey}
                                {@const fVal = inspectingItem.listing?.[fKey]}
                                {@const fProv =
                                    inspectingItem.provenance?.[fKey] || {}}
                                <div class="provenance-card">
                                    <div
                                        class="flex items-center justify-between"
                                    >
                                        <span
                                            class="font-bold text-xs uppercase text-slate-600"
                                            >{fKey}</span
                                        >
                                        <div class="flex items-center gap-1.5">
                                            <span
                                                class="prov-badge prov-{fProv.source ||
                                                    'missing'}"
                                            >
                                                {fProv.source || "missing"}
                                            </span>
                                            {#if fProv.confidence}
                                                <span
                                                    class="conf-badge conf-{fProv.confidence}"
                                                >
                                                    {fProv.confidence}
                                                </span>
                                            {/if}
                                        </div>
                                    </div>
                                    <div
                                        class="text-sm text-slate-800 font-medium mt-1 truncate"
                                    >
                                        {fVal || "(Empty)"}
                                    </div>
                                    {#if fProv.evidence}
                                        <p
                                            class="text-xs text-slate-500 mt-0.5 italic"
                                        >
                                            {fProv.evidence}
                                        </p>
                                    {/if}
                                    <div
                                        class="mt-2 flex items-center justify-between pt-1 border-t border-slate-100"
                                    >
                                        <label
                                            class="flex items-center gap-1.5 text-xs text-slate-700 cursor-pointer"
                                        >
                                            <input
                                                type="checkbox"
                                                checked={fProv.sellerConfirmed ===
                                                    true}
                                                on:change={(e) => {
                                                    if (e.target.checked) {
                                                        handleInlineEdit(
                                                            inspectingItem,
                                                            "confirmField",
                                                            fKey,
                                                        );
                                                    }
                                                }}
                                            />
                                            <span>Seller Confirmed</span>
                                        </label>
                                    </div>
                                </div>
                            {/each}
                        </div>
                    </div>

                    <!-- Description Preview -->
                    <div class="inspector-section">
                        <h4 class="inspector-section-title">Condition Note</h4>
                        <textarea
                            class="w-full text-xs p-2 border rounded-md h-20 text-slate-700"
                            value={inspectingItem.listing?.cnote || ""}
                            on:input={(e) =>
                                handleInlineEdit(
                                    inspectingItem,
                                    "cnote",
                                    e.target.value,
                                )}
                        ></textarea>
                    </div>
                </div>
            </aside>
        {/if}
    </div>

    <!-- Sticky Bottom Action Bar with State-Dependent Main Action (Requirements 4, 5, 7) -->
    <div class="bottom-action-bar">
        <div class="action-bar-inner">
            <!-- Dedicated Upload Photos entry point (Requirement 6) -->
            <button
                type="button"
                class="action-btn-outline"
                on:click={openUploadModal}
                disabled={actionLoading}
            >
                Upload photos
            </button>

            <!-- State-Dependent Main Action -->
            {#if hasAnalyzing}
                <button
                    type="button"
                    class="action-btn-primary"
                    disabled={true}
                >
                    Analyzing…
                </button>
            {:else if hasGrouped}
                <button
                    type="button"
                    class="action-btn-primary"
                    on:click={analyzeSelectedOrBatch}
                    disabled={actionLoading || items.length === 0}
                >
                    Analyze photos
                </button>
            {:else if hasMissingRequired}
                <button
                    type="button"
                    class="action-btn-primary"
                    style="background: #e11d48;"
                    on:click={fixHighlightedFields}
                    disabled={actionLoading}
                >
                    Fix highlighted fields
                </button>
            {:else if hasNeedsReview}
                <button
                    type="button"
                    class="action-btn-primary"
                    on:click={reviewSelectedOrFirst}
                    disabled={actionLoading}
                >
                    Review and edit
                </button>
            {:else if hasValidated}
                <button
                    type="button"
                    class="action-btn-approve"
                    on:click={approveSelected}
                    disabled={actionLoading}
                >
                    Approve selected ({selectedItemIds.size > 0 ? selectedItemIds.size : items.filter(it => it.rulesCheck?.valid).length})
                </button>
            {:else if allApproved}
                <div style="display: flex; gap: 8px; align-items: center;">
                    <button
                        type="button"
                        class="action-btn-submit"
                        on:click={submitSelectedDrafts}
                        disabled={actionLoading}
                    >
                        Create eBay drafts
                    </button>
                    <button
                        type="button"
                        class="action-btn-secondary-export"
                        title="Generate official eBay Seller Hub Reports Create Drafts CSV from approved items"
                        on:click={handleDownloadDraftCsv}
                        disabled={exportCsvLoading}
                    >
                        {exportCsvLoading ? "Generating CSV..." : "Download Seller Hub Draft CSV"}
                    </button>
                </div>
            {:else if allSubmitted}
                <button
                    type="button"
                    class="action-btn-outline"
                    on:click={viewDraftResult}
                    disabled={actionLoading}
                >
                    View draft result
                </button>
            {:else}
                <button
                    type="button"
                    class="action-btn-primary"
                    on:click={validateSelected}
                    disabled={actionLoading || items.length === 0}
                >
                    Validate selected ({selectedItemIds.size > 0 ? selectedItemIds.size : items.length})
                </button>
            {/if}

            {#if approvedCount > 0 && !allApproved}
                <button
                    type="button"
                    class="action-btn-secondary-export"
                    title="Generate official eBay Seller Hub Reports Create Drafts CSV from approved items"
                    on:click={handleDownloadDraftCsv}
                    disabled={exportCsvLoading}
                >
                    {exportCsvLoading ? "Generating CSV..." : `Download Seller Hub Draft CSV (${approvedCount} approved)`}
                </button>
            {/if}
        </div>
    </div>
</div>

<!-- Upload & Grouping Modal Drawer -->
{#if isUploadOpen}
    <div class="modal-backdrop">
        <button
            type="button"
            class="modal-backdrop-btn"
            aria-label="Close modal overlay"
            tabindex="-1"
            on:click={closeUploadModal}
        />
        <div
            class="modal-box"
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
        >
            <div class="modal-header">
                <div>
                    <h3
                        id="modal-title"
                        class="text-lg font-bold text-slate-900"
                    >
                        Upload Photos & Group Items
                    </h3>
                    <p class="text-xs text-slate-500">
                        Staging batch: {selectedBatch?.name}
                    </p>
                </div>
                <button
                    class="close-btn"
                    aria-label="Close modal"
                    on:click={closeUploadModal}>✕</button
                >
            </div>

            <div class="modal-body">
                <!-- Grouping Mode Selector -->
                <div class="mb-4">
                    <span class="block text-xs font-bold text-slate-700 mb-1"
                        >Expected photos per item:</span
                    >
                    <div class="flex gap-2">
                        {#each [["1", "1 photo"], ["2", "2 photos"], ["3", "3 photos"], ["auto", "Automatic"]] as [mVal, mLabel]}
                            <button
                                type="button"
                                class="px-3 py-1.5 rounded-lg border text-xs font-semibold"
                                class:active-group-mode={uploadGroupingMode ===
                                    mVal}
                                on:click={() => (uploadGroupingMode = mVal)}
                            >
                                {mLabel}
                            </button>
                        {/each}
                    </div>
                    <p class="text-xs text-slate-500 mt-1">
                        Select multiple photos. HHT will automatically bundle
                        photos into item groups.
                    </p>
                </div>

                <!-- Dropzone -->
                <div
                    class="dropzone-box"
                    role="region"
                    aria-label="Photo dropzone"
                    on:dragover|preventDefault
                    on:drop={handleDrop}
                >
                    <input
                        type="file"
                        multiple
                        accept="image/*"
                        class="hidden"
                        id="batch-photo-upload"
                        on:change={handleFilesSelected}
                    />
                    <label
                        for="batch-photo-upload"
                        class="cursor-pointer block text-center py-6"
                    >
                        <div class="text-3xl mb-2">📸</div>
                        <p class="text-sm font-bold text-slate-800">
                            Click to choose or drag images here
                        </p>
                        <p class="text-xs text-slate-500 mt-1">
                            Supports JPEG, PNG, WebP, HEIC. Large photos are
                            automatically compressed before upload (max 10MB
                            request).
                        </p>
                    </label>
                </div>

                <!-- Previews Grid -->
                {#if uploadPreviews.length > 0}
                    <div class="mt-4">
                        <div class="flex justify-between items-center mb-2">
                            <div>
                                <span class="text-xs font-bold text-slate-700"
                                    >{uploadPreviews.length} photo(s) selected</span
                                >
                                <span class="text-[11px] text-slate-500 ml-2">
                                    Total original: {formatBytes(
                                        uploadPreviews.reduce(
                                            (acc, p) =>
                                                acc + (p.originalSize || 0),
                                            0,
                                        ),
                                    )}
                                </span>
                            </div>
                            <button
                                class="text-xs text-rose-600 font-bold"
                                on:click={() => {
                                    uploadPreviews.forEach((p) => {
                                        if (p.url) URL.revokeObjectURL(p.url);
                                    });
                                    uploadFiles = [];
                                    uploadPreviews = [];
                                }}
                                disabled={isUploading}>Clear</button
                            >
                        </div>
                        <div
                            class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2.5 max-h-56 overflow-y-auto p-2 border border-slate-200 rounded-lg bg-slate-50/50"
                        >
                            {#each uploadPreviews as prev, idx}
                                <div
                                    class="relative group rounded-lg overflow-hidden border border-slate-200 bg-white p-1.5 shadow-xs flex flex-col gap-1.5"
                                >
                                    <div
                                        class="relative aspect-video rounded overflow-hidden bg-slate-100"
                                    >
                                        <img
                                            src={prev.url}
                                            alt={prev.name}
                                            class="w-full h-full object-cover"
                                        />
                                        <span
                                            class="absolute top-1 left-1 bg-black/70 text-white text-[10px] font-bold px-1.5 py-0.5 rounded"
                                        >
                                            #{idx + 1}
                                        </span>
                                        {#if prev.isOversized}
                                            <span
                                                class="absolute top-1 right-1 bg-amber-500/90 text-white text-[9px] font-bold px-1 rounded"
                                                title="Over 8MB - will be automatically compressed before upload"
                                            >
                                                Auto-compress
                                            </span>
                                        {/if}
                                    </div>
                                    <div class="px-0.5">
                                        <p
                                            class="text-[11px] font-medium text-slate-800 truncate"
                                            title={prev.name}
                                        >
                                            {prev.name}
                                        </p>
                                        <div
                                            class="flex items-center justify-between text-[10px] text-slate-500 mt-0.5"
                                        >
                                            <span
                                                >Orig: {prev.formattedOriginalSize}</span
                                            >
                                            <span
                                                class="font-semibold {prev.uploadSize
                                                    ? 'text-emerald-700'
                                                    : 'text-slate-600'}"
                                            >
                                                {prev.uploadSize
                                                    ? `Upload: ${prev.formattedUploadSize}`
                                                    : prev.formattedUploadSize}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            {/each}
                        </div>
                    </div>
                {/if}

                {#if isUploading}
                    <div
                        class="mt-3 p-2.5 rounded-lg bg-sky-50 border border-sky-200 text-xs text-sky-800 flex items-center gap-2"
                    >
                        <span class="animate-spin">⏳</span>
                        <span class="font-semibold"
                            >{uploadPhase === "compressing"
                                ? "Compressing photos..."
                                : "Uploading photos..."}</span
                        >
                        <span class="text-sky-600">{uploadPhaseMessage}</span>
                    </div>
                {/if}

                {#if uploadError}
                    <div class="notice error mt-3">
                        <p>{uploadError}</p>
                    </div>
                {/if}
            </div>

            <div class="modal-footer">
                <button
                    class="px-4 py-2 border rounded-lg text-xs font-bold"
                    on:click={closeUploadModal}
                    disabled={isUploading}
                >
                    Cancel
                </button>
                <button
                    class="px-5 py-2 bg-slate-900 text-white rounded-lg text-xs font-bold hover:bg-slate-800 disabled:opacity-50"
                    on:click={submitUploadAndGroup}
                    disabled={isUploading || uploadFiles.length === 0}
                >
                    {#if uploadPhase === "compressing"}
                        Compressing...
                    {:else if uploadPhase === "uploading"}
                        Uploading...
                    {:else if isUploading}
                        Uploading & Grouping...
                    {:else}
                        Create Groups ({uploadFiles.length} photos)
                    {/if}
                </button>
            </div>
        </div>
    </div>
{/if}

<!-- Official eBay Seller Hub Draft Template Configuration Modal -->
{#if isTemplateModalOpen}
    <div class="modal-backdrop">
        <button
            type="button"
            class="modal-backdrop-btn"
            aria-label="Close modal overlay"
            tabindex="-1"
            on:click={() => (isTemplateModalOpen = false)}
        />
        <div
            class="modal-box"
            role="dialog"
            aria-modal="true"
            aria-labelledby="template-modal-title"
            style="max-width: 540px;"
        >
            <div class="modal-header">
                <div>
                    <h3 id="template-modal-title" class="text-lg font-bold text-slate-900">
                        Official eBay Seller Hub Draft Template
                    </h3>
                    <p class="text-xs text-slate-500">
                        Configure source schema for Seller Hub Reports Draft CSV exports
                    </p>
                </div>
                <button
                    class="close-btn"
                    aria-label="Close modal"
                    on:click={() => (isTemplateModalOpen = false)}
                >✕</button>
            </div>
            <div class="modal-body space-y-4">
                <div class="p-3 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-900 space-y-1.5">
                    <p class="font-bold">How to download the current eBay template:</p>
                    <ol class="list-decimal pl-4 space-y-1">
                        <li>Go to <strong>eBay Seller Hub → Reports → Upload</strong></li>
                        <li>Click <strong>Get template</strong></li>
                        <li>Select <strong>Listings</strong> → <strong>Create drafts template</strong></li>
                        <li>Download the CSV file and select it below</li>
                    </ol>
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">
                        Upload Downloaded eBay Template CSV:
                        <input
                            type="file"
                            accept=".csv"
                            class="w-full text-xs p-2 border rounded-lg bg-slate-50 mt-1 font-normal"
                            on:change={handleTemplateFileUpload}
                        />
                    </label>
                </div>
                {#if templateFileName}
                    <div class="p-2 bg-emerald-50 border border-emerald-200 rounded text-xs text-emerald-800">
                        Active configured template: <strong>{templateFileName}</strong>
                    </div>
                {:else}
                    <div class="p-2 bg-slate-50 border border-slate-200 rounded text-xs text-slate-600">
                        Using built-in official eBay Seller Hub Drafts schema with standard eBay headers and #INFO metadata.
                    </div>
                {/if}
            </div>
            <div class="modal-footer">
                <button
                    class="px-4 py-2 border rounded-lg text-xs font-bold"
                    on:click={() => (isTemplateModalOpen = false)}
                >
                    Close
                </button>
            </div>
        </div>
    </div>
{/if}

{#if isDraftResultModalOpen}
    <div class="modal-backdrop">
        <button
            type="button"
            class="modal-backdrop-dismiss"
            aria-label="Dismiss modal"
            on:click={() => (isDraftResultModalOpen = false)}
        ></button>
        <div class="modal-card max-w-lg">
            <div class="modal-header">
                <div>
                    <h3 class="text-base font-bold text-slate-900">
                        eBay Draft Results
                    </h3>
                    <p class="text-xs text-slate-500">
                        Seller Hub Draft Feed Submission Status
                    </p>
                </div>
                <button
                    class="close-btn"
                    aria-label="Close modal"
                    on:click={() => (isDraftResultModalOpen = false)}
                >✕</button>
            </div>
            <div class="modal-body space-y-4">
                <div class="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-center">
                    <div class="text-3xl mb-1">🎉</div>
                    <h4 class="text-base font-bold text-emerald-900">
                        eBay Drafts Created Successfully!
                    </h4>
                    <p class="text-xs text-emerald-700 mt-1">
                        Your approved listings have been sent directly to eBay Seller Hub as drafts.
                    </p>
                </div>

                <div class="bg-slate-50 p-3.5 rounded-lg border border-slate-200 text-xs space-y-2.5">
                    <div class="flex justify-between items-center pb-1.5 border-b border-slate-200/70">
                        <span class="text-slate-500 font-medium">Batch:</span>
                        <strong class="text-slate-800">{selectedBatch?.name || "Active Batch"}</strong>
                    </div>
                    <div class="flex justify-between items-center pb-1.5 border-b border-slate-200/70">
                        <span class="text-slate-500 font-medium">Drafts Submitted:</span>
                        <strong class="text-emerald-700 font-bold">{lastDraftResult?.submittedCount || submittedCount || approvedCount} item(s)</strong>
                    </div>
                    {#if lastDraftResult?.feedTaskId}
                        <div class="flex justify-between items-center pb-1.5 border-b border-slate-200/70">
                            <span class="text-slate-500 font-medium">eBay Feed Task ID:</span>
                            <span class="font-mono text-slate-800 bg-white px-1.5 py-0.5 rounded border border-slate-200">{lastDraftResult.feedTaskId}</span>
                        </div>
                    {/if}
                    <div class="flex justify-between items-center">
                        <span class="text-slate-500 font-medium">Mode:</span>
                        <span class="text-emerald-800 font-semibold bg-emerald-100/60 px-2 py-0.5 rounded">
                            Seller Hub Draft (No live listings published)
                        </span>
                    </div>
                </div>

                <div class="p-3 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-900">
                    <p class="font-bold mb-1">Where to find your drafts in eBay:</p>
                    <p class="text-slate-700">
                        Go to <strong>eBay Seller Hub → Listings → Drafts</strong>.
                        Your items are pre-populated with titles, prices, categories, and item specifics ready for you to schedule or publish whenever you want.
                    </p>
                </div>
            </div>
            <div class="modal-footer flex items-center justify-between">
                <button
                    type="button"
                    class="action-btn-secondary-export"
                    title="Download the official Seller Hub Create Drafts CSV backup"
                    on:click={handleDownloadDraftCsv}
                    disabled={exportCsvLoading}
                >
                    {exportCsvLoading ? "Generating CSV..." : "Download Backup CSV"}
                </button>
                <button
                    type="button"
                    class="action-btn-primary px-4 py-2 text-xs"
                    on:click={() => (isDraftResultModalOpen = false)}
                >
                    Done
                </button>
            </div>
        </div>
    </div>
{/if}

<style>
    .workspace-root {
        display: flex;
        flex-direction: column;
        gap: 12px;
        min-height: calc(100vh - 120px);
        background: #ffffff;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        position: relative;
    }

    .workspace-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding-bottom: 12px;
        border-bottom: 1px solid #f1f5f9;
    }

    .selection-badge {
        font-size: 13px;
        font-weight: 700;
        background: #0f172a;
        color: #ffffff;
        padding: 4px 12px;
        border-radius: 999px;
    }

    .header-right {
        display: flex;
        align-items: center;
        gap: 16px;
    }

    .subtab-toggle-container {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
        padding: 4px 0;
    }

    .subtab-pill-box {
        display: inline-flex;
        background: #f1f5f9;
        padding: 4px;
        border-radius: 999px;
        gap: 4px;
    }

    .subtab-pill {
        border: none;
        background: transparent;
        color: #64748b;
        font-size: 13px;
        font-weight: 600;
        padding: 6px 18px;
        border-radius: 999px;
        cursor: pointer;
        transition: all 0.15s ease;
        min-height: auto;
    }

    .subtab-pill.active {
        background: #ffffff;
        color: #0f172a;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
    }

    .batch-selector-box {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .batch-select {
        font-size: 13px;
        padding: 5px 10px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
    }

    .batch-new-btn {
        min-height: auto;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 600;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #f8fafc;
    }

    .instruction-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 13px;
        color: #64748b;
        padding: 6px 0;
    }

    .search-input {
        font-size: 12px;
        padding: 6px 10px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        width: 200px;
    }

    .filter-select {
        font-size: 12px;
        padding: 6px 10px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
    }

    /* View Mode Segmented Toggle */
    .view-mode-toggle {
        display: inline-flex;
        background: #f1f5f9;
        padding: 2px;
        border-radius: 8px;
        border: 1px solid #e2e8f0;
        gap: 2px;
    }

    .view-toggle-btn {
        border: none;
        background: transparent;
        color: #64748b;
        font-size: 12px;
        font-weight: 600;
        padding: 4px 10px;
        border-radius: 6px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        transition: all 0.15s ease;
        min-height: auto;
    }

    .view-toggle-btn:hover {
        color: #0f172a;
    }

    .view-toggle-btn.active {
        background: #ffffff;
        color: #0f172a;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
    }

    /* Cards Grid View (MetaMuse-style) */
    .cards-grid-container {
        flex: 1;
        overflow-y: auto;
        max-height: calc(100vh - 280px);
        padding: 16px;
        background: #f8fafc;
    }

    .cards-empty-state {
        text-align: center;
        padding: 60px 20px;
        color: #64748b;
    }

    .cards-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
        gap: 16px;
        align-items: start;
    }

    .listing-card {
        background: #ffffff;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
        display: flex;
        flex-direction: column;
        transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
    }

    .listing-card:hover {
        border-color: #cbd5e1;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.07);
        transform: translateY(-2px);
    }

    .listing-card.selected {
        border-color: #0f172a;
        box-shadow: 0 0 0 2px #0f172a;
    }

    .listing-card.inspecting {
        border-color: #2563eb;
        box-shadow: 0 0 0 2px #2563eb;
    }

    .listing-card.is-approved {
        border-left: 4px solid #0d9488;
    }

    .card-header-container {
        position: relative;
        aspect-ratio: 4 / 3;
        width: 100%;
        background: #f1f5f9;
        overflow: hidden;
    }

    .card-media {
        display: block;
        width: 100%;
        height: 100%;
        padding: 0;
        margin: 0;
        border: none;
        background: transparent;
        cursor: pointer;
        min-height: auto;
    }

    .card-img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        transition: transform 0.2s ease;
    }

    .card-media:hover .card-img {
        transform: scale(1.03);
    }

    .card-img-placeholder {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 32px;
        color: #94a3b8;
    }

    .card-overlay-top {
        position: absolute;
        top: 8px;
        left: 8px;
        right: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        z-index: 2;
    }

    .card-checkbox-label {
        background: rgba(255, 255, 255, 0.9);
        backdrop-filter: blur(4px);
        border-radius: 6px;
        padding: 4px 6px;
        display: flex;
        align-items: center;
        cursor: pointer;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
    }

    .card-photo-count {
        background: rgba(15, 23, 42, 0.75);
        backdrop-filter: blur(4px);
        color: #ffffff;
        font-size: 11px;
        font-weight: 700;
        padding: 3px 8px;
        border-radius: 999px;
    }

    .card-readiness-badge {
        position: absolute;
        bottom: 8px;
        left: 8px;
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(4px);
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 5px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
        color: #334155;
    }

    .card-readiness-badge.ready {
        color: #065f46;
        background: rgba(236, 253, 245, 0.95);
        border: 1px solid #a7f3d0;
    }

    .readiness-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        display: inline-block;
    }

    .card-content {
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        flex: 1;
    }

    .card-category-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
    }

    .card-category-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        text-align: left;
        font-size: 11px;
        font-weight: 600;
        color: #1e293b;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 3px 8px;
        max-width: 210px;
        text-overflow: ellipsis;
        overflow: hidden;
        white-space: nowrap;
        cursor: pointer;
        min-height: auto;
    }

    .card-category-btn:hover {
        background: #f1f5f9;
        border-color: #cbd5e1;
    }

    .cond-pill {
        font-size: 10px;
        font-weight: 700;
        padding: 2px 6px;
        border-radius: 4px;
        text-transform: uppercase;
        letter-spacing: 0.02em;
    }

    .cond-new {
        background: #dcfce7;
        color: #166534;
    }

    .cond-preowned {
        background: #f1f5f9;
        color: #475569;
    }

    .card-title-input {
        width: 100%;
        font-size: 13px;
        font-weight: 600;
        color: #0f172a;
        padding: 6px 8px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
        box-sizing: border-box;
    }

    .card-title-input:focus {
        border-color: #2563eb;
        outline: none;
        box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
    }

    .card-cand-toggle-btn {
        background: none;
        border: none;
        font-size: 11px;
        font-weight: 600;
        color: #2563eb;
        cursor: pointer;
        padding: 0;
        min-height: auto;
    }

    .card-cand-toggle-btn:hover {
        text-decoration: underline;
    }

    .card-specifics-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
    }

    .spec-chip {
        font-size: 10px;
        background: #f8fafc;
        color: #334155;
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid #e2e8f0;
    }

    .card-price-section {
        background: #f8fafc;
        border-radius: 8px;
        padding: 8px;
        border: 1px solid #f1f5f9;
    }

    .card-price-input {
        width: 84px;
        font-size: 14px;
        font-weight: 800;
        color: #065f46;
        padding: 4px 6px 4px 16px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        text-align: right;
        background: #ffffff;
        box-sizing: border-box;
    }

    .card-price-tiers {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 4px;
        margin-top: 6px;
    }

    .price-tier-chip {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 4px 2px;
        border-radius: 6px;
        border: 1px solid #e2e8f0;
        background: #ffffff;
        cursor: pointer;
        transition: all 0.15s ease;
        min-height: auto;
    }

    .price-tier-chip:hover {
        background: #f1f5f9;
        border-color: #cbd5e1;
    }

    .price-tier-chip .tier-lbl {
        font-size: 9px;
        font-weight: 700;
        color: #64748b;
        text-transform: uppercase;
    }

    .price-tier-chip .tier-cost {
        font-size: 11px;
        font-weight: 700;
        color: #0f172a;
    }

    .card-footer {
        padding: 10px 12px;
        border-top: 1px solid #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        background: #ffffff;
    }

    .card-action-btn-secondary {
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 600;
        color: #334155;
        background: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        cursor: pointer;
        min-height: auto;
    }

    .card-action-btn-secondary:hover {
        background: #f1f5f9;
        border-color: #94a3b8;
    }

    .card-action-btn-approve {
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 700;
        color: #ffffff;
        background: #0f766e;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        transition: background 0.15s ease;
        min-height: auto;
    }

    .card-action-btn-approve:hover {
        background: #0d9488;
    }

    .card-action-btn-retry {
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 700;
        color: #ffffff;
        background: #e11d48;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        transition: background 0.15s ease;
        min-height: auto;
    }

    .card-action-btn-retry:hover {
        background: #be123c;
    }

    .approved-badge {
        font-size: 11px;
        font-weight: 700;
        color: #0d9488;
        background: #ccfbf1;
        padding: 4px 10px;
        border-radius: 999px;
        border: 1px solid #99f6e4;
    }

    .draft-submitted-badge {
        font-size: 11px;
        font-weight: 700;
        color: #0891b2;
        background: #cffafe;
        padding: 4px 10px;
        border-radius: 999px;
        border: 1px solid #a5f3fc;
    }

    /* Inspector Readiness Health Check */
    .inspector-readiness-banner {
        margin-bottom: 14px;
        padding: 12px;
        border-radius: 8px;
        background: #fffbeb;
        border: 1px solid #fef3c7;
    }

    .inspector-readiness-banner.is-ready {
        background: #ecfdf5;
        border-color: #a7f3d0;
    }

    .readiness-check-item {
        display: flex;
        align-items: center;
        gap: 4px;
        color: #94a3b8;
        font-weight: 600;
    }

    .readiness-check-item.passed {
        color: #065f46;
        font-weight: 700;
    }

    .spreadsheet-container {
        display: flex;
        gap: 16px;
        position: relative;
        overflow: hidden;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        min-height: 400px;
    }

    .table-scroll-wrapper {
        flex: 1;
        overflow-x: auto;
        overflow-y: auto;
        max-height: 580px;
    }

    .inventory-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 13px;
        text-align: left;
    }

    .inventory-table thead {
        position: sticky;
        top: 0;
        background: #f8fafc;
        z-index: 10;
        border-bottom: 1px solid #e2e8f0;
    }

    .inventory-table th {
        padding: 10px 12px;
        font-size: 12px;
        font-weight: 700;
        color: #475569;
        border-bottom: 1px solid #e2e8f0;
        cursor: pointer;
        user-select: none;
    }

    .inventory-table td {
        padding: 10px 12px;
        border-bottom: 1px solid #f1f5f9;
        vertical-align: middle;
        background: #ffffff;
    }

    .inventory-table tr:hover td {
        background: #f8fafc;
    }

    .selected-row td {
        background: #f0fdf4 !important;
    }

    .inspecting-row td {
        border-left: 3px solid #2563eb;
    }

    .row-checkbox {
        width: 16px;
        height: 16px;
        cursor: pointer;
        accent-color: #0f172a;
    }

    .thumb-cell {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        width: 64px;
    }

    .thumb-box {
        width: 52px;
        height: 52px;
        border-radius: 8px;
        overflow: hidden;
        background: #f1f5f9;
        border: 1px solid #e2e8f0;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .thumb-img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    .thumb-placeholder {
        font-size: 20px;
        color: #94a3b8;
    }

    .photo-count-label {
        font-size: 10px;
        color: #64748b;
        font-weight: 600;
    }

    .table-input {
        width: 100%;
        font-size: 13px;
        padding: 6px 10px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
        outline: none;
        transition: border-color 0.15s;
    }

    .table-input:focus {
        border-color: #2563eb;
        box-shadow: 0 0 0 1px #2563eb;
    }

    .missing-input {
        border-color: #f59e0b;
        background-color: #fffbeb;
        color: #b45309;
    }

    .candidate-toggle-btn {
        position: absolute;
        right: 6px;
        top: 50%;
        transform: translateY(-50%);
        min-height: auto;
        padding: 2px 4px;
        border: none;
        background: transparent;
        cursor: pointer;
        font-size: 13px;
    }

    .candidate-dropdown {
        position: absolute;
        top: 100%;
        left: 0;
        width: 320px;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        z-index: 50;
        margin-top: 4px;
        max-height: 240px;
        overflow-y: auto;
    }

    .candidate-option-btn {
        width: 100%;
        text-align: left;
        padding: 8px 10px;
        border: none;
        border-bottom: 1px solid #f1f5f9;
        background: #ffffff;
        cursor: pointer;
        min-height: auto;
    }

    .candidate-option-btn:hover {
        background: #f8fafc;
    }

    .price-tiers-btn {
        min-height: auto;
        padding: 4px 6px;
        border: none;
        background: transparent;
        cursor: pointer;
        font-size: 13px;
    }

    .status-pill {
        display: inline-block;
        font-size: 11px;
        font-weight: 700;
        padding: 3px 10px;
        border-radius: 999px;
        border-width: 1px;
        white-space: nowrap;
    }

    /* Side-panel Inspector */
    .inspector-drawer {
        width: 380px;
        border-left: 1px solid #e2e8f0;
        background: #ffffff;
        display: flex;
        flex-direction: column;
        z-index: 20;
    }

    .inspector-header {
        padding: 14px 16px;
        border-bottom: 1px solid #e2e8f0;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .full-review-btn {
        background: #ffffff;
        border: 1px solid #cbd5e1;
        color: #334155;
        font-size: 11px;
        font-weight: 600;
        padding: 4px 10px;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .full-review-btn:hover {
        background: #f8fafc;
        border-color: #94a3b8;
        color: #0f172a;
    }

    .close-btn {
        min-height: auto;
        padding: 4px 8px;
        border: none;
        background: transparent;
        color: #94a3b8;
        font-size: 14px;
        cursor: pointer;
    }

    .inspector-body {
        padding: 16px;
        overflow-y: auto;
        max-height: 540px;
        display: flex;
        flex-direction: column;
        gap: 16px;
    }

    .inspector-section-title {
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        color: #475569;
        margin-bottom: 8px;
    }

    .inspector-photo-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 8px;
    }

    .inspector-photo-card {
        position: relative;
        aspect-ratio: 1;
        border-radius: 6px;
        overflow: hidden;
        border: 1px solid #e2e8f0;
    }

    .inspector-photo-card.is-cover {
        border-color: #2563eb;
        box-shadow: 0 0 0 2px #2563eb;
    }

    .inspector-img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    .cover-tag {
        position: absolute;
        top: 4px;
        left: 4px;
        background: #2563eb;
        color: #ffffff;
        font-size: 9px;
        font-weight: 700;
        padding: 1px 5px;
        border-radius: 4px;
    }

    .photo-pos {
        position: absolute;
        bottom: 4px;
        right: 4px;
        background: rgba(0, 0, 0, 0.6);
        color: #ffffff;
        font-size: 9px;
        font-weight: 600;
        padding: 1px 5px;
        border-radius: 4px;
    }

    .provenance-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 8px 10px;
    }

    .prov-badge {
        font-size: 9px;
        font-weight: 700;
        padding: 1px 5px;
        border-radius: 4px;
        text-transform: uppercase;
    }

    .prov-photo_evidence {
        background: #dbeafe;
        color: #1e40af;
    }
    .prov-ai_estimate {
        background: #e0e7ff;
        color: #3730a3;
    }
    .prov-seller_input {
        background: #f3e8ff;
        color: #6b21a8;
    }
    .prov-missing {
        background: #fef3c7;
        color: #92400e;
    }

    .conf-badge {
        font-size: 9px;
        font-weight: 600;
        padding: 1px 4px;
        border-radius: 4px;
    }

    .conf-high {
        background: #d1fae5;
        color: #065f46;
    }
    .conf-medium {
        background: #fef3c7;
        color: #92400e;
    }
    .conf-low {
        background: #fee2e2;
        color: #991b1b;
    }
    .conf-none {
        background: #f1f5f9;
        color: #64748b;
    }

    /* Bottom Action Bar matching Mockup */
    .bottom-action-bar {
        position: sticky;
        bottom: 0;
        background: #ffffff;
        padding: 12px 0 0;
        border-top: 1px solid #f1f5f9;
        z-index: 10;
    }

    .action-bar-inner {
        display: flex;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
    }

    .action-btn-outline {
        min-height: 42px;
        padding: 0 20px;
        font-size: 13px;
        font-weight: 700;
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        background: #ffffff;
        color: #0f172a;
        cursor: pointer;
        transition: background 0.15s;
    }

    .action-btn-outline:hover:not(:disabled) {
        background: #f8fafc;
    }

    .action-btn-primary {
        min-height: 42px;
        padding: 0 24px;
        font-size: 13px;
        font-weight: 700;
        border: none;
        border-radius: 999px;
        background: #0f172a;
        color: #ffffff;
        cursor: pointer;
        transition: opacity 0.15s;
    }

    .action-btn-approve {
        min-height: 42px;
        padding: 0 20px;
        font-size: 13px;
        font-weight: 700;
        border: none;
        border-radius: 999px;
        background: #0f766e;
        color: #ffffff;
        cursor: pointer;
        transition: opacity 0.15s;
    }

    .action-btn-submit {
        min-height: 42px;
        padding: 0 20px;
        font-size: 13px;
        font-weight: 700;
        border: none;
        border-radius: 999px;
        background: #2563eb;
        color: #ffffff;
        cursor: pointer;
        transition: opacity 0.15s;
    }

    .action-btn-primary:hover:not(:disabled),
    .action-btn-approve:hover:not(:disabled),
    .action-btn-submit:hover:not(:disabled) {
        opacity: 0.9;
    }

    /* Modal Styles */
    .modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.5);
        backdrop-filter: blur(2px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 100;
        padding: 20px;
    }

    .modal-backdrop-btn {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        background: transparent;
        border: none;
        cursor: default;
        padding: 0;
        margin: 0;
    }

    .modal-box {
        position: relative;
        z-index: 1;
        background: #ffffff;
        border-radius: 12px;
        width: min(100%, 600px);
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
        overflow: hidden;
    }

    .modal-header {
        padding: 16px 20px;
        border-bottom: 1px solid #e2e8f0;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .modal-body {
        padding: 20px;
    }

    .modal-footer {
        padding: 12px 20px;
        border-top: 1px solid #e2e8f0;
        background: #f8fafc;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 10px;
    }

    .active-group-mode {
        background: #0f172a !important;
        color: #ffffff !important;
        border-color: #0f172a !important;
    }

    .dropzone-box {
        border: 2px dashed #cbd5e1;
        border-radius: 8px;
        background: #f8fafc;
        transition: border-color 0.15s;
    }

    .dropzone-box:hover {
        border-color: #2563eb;
    }

    /* Batch Actions Dropdown */
    .batch-actions-dropdown {
        position: relative;
        display: inline-block;
    }

    .batch-actions-trigger-btn {
        padding: 5px 12px;
        font-size: 12px;
        font-weight: 600;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
        color: #334155;
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .batch-actions-trigger-btn:hover {
        background: #f8fafc;
        border-color: #94a3b8;
    }

    .batch-actions-menu {
        position: absolute;
        right: 0;
        top: 100%;
        margin-top: 4px;
        width: 260px;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        z-index: 50;
        padding: 4px 0;
    }

    .menu-item {
        display: block;
        width: 100%;
        text-align: left;
        padding: 8px 14px;
        font-size: 12px;
        font-weight: 500;
        color: #334155;
        background: transparent;
        border: none;
        cursor: pointer;
        transition: background 0.1s ease;
    }

    .menu-item:hover:not(:disabled) {
        background: #f1f5f9;
        color: #0f172a;
    }

    .menu-item:disabled {
        opacity: 0.45;
        cursor: not-allowed;
    }

    .menu-item.menu-danger {
        color: #dc2626;
    }

    .menu-item.menu-danger:hover:not(:disabled) {
        background: #fef2f2;
        color: #b91c1c;
    }

    .menu-divider {
        margin: 4px 0;
        border: none;
        border-top: 1px solid #e2e8f0;
    }

    /* Batch Lifecycle Summary Bar */
    .batch-lifecycle-summary {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        margin-bottom: 4px;
    }

    .lifecycle-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 999px;
        background: #ffffff;
        border: 1px solid #e2e8f0;
        color: #475569;
    }

    .lifecycle-chip.highlight {
        background: #eff6ff;
        border-color: #bfdbfe;
        color: #1d4ed8;
    }

    .chip-label {
        color: #64748b;
    }

    .chip-value {
        font-weight: 700;
        color: #0f172a;
    }

    /* Price Tiers in Inspector */
    .price-tiers-inspector-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 6px;
    }

    .price-tier-btn {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 6px 4px;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .price-tier-btn:hover {
        background: #f1f5f9;
        border-color: #cbd5e1;
    }

    .price-tier-btn .tier-name {
        font-size: 10px;
        font-weight: 600;
        color: #64748b;
        text-transform: uppercase;
    }

    .price-tier-btn .tier-val {
        font-size: 13px;
        font-weight: 700;
        color: #0f172a;
        margin-top: 2px;
    }

    /* Category Suggestions Dropdown */
    .category-suggestions-dropdown {
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        z-index: 60;
        max-height: 220px;
        overflow-y: auto;
    }

    .sug-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        width: 100%;
        text-align: left;
        padding: 6px 10px;
        border: none;
        border-bottom: 1px solid #f1f5f9;
        background: transparent;
        cursor: pointer;
        font-size: 11px;
    }

    .sug-item:hover {
        background: #f8fafc;
    }

    .sug-path {
        color: #1e293b;
        font-weight: 500;
    }

    .sug-id {
        color: #64748b;
        font-family: monospace;
        font-size: 10px;
    }

    /* Aspects in Inspector */
    .aspect-field-card {
        padding: 8px 10px;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        background: #f8fafc;
    }

    .aspect-val-pill {
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 999px;
        border: 1px solid #cbd5e1;
        background: #ffffff;
        color: #334155;
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .aspect-val-pill:hover {
        background: #f1f5f9;
    }

    .aspect-val-pill.selected {
        background: #0f172a;
        color: #ffffff;
        border-color: #0f172a;
    }

    /* Secondary CSV Export Button */
    .action-btn-secondary-export {
        min-height: 42px;
        padding: 0 18px;
        font-size: 13px;
        font-weight: 700;
        border: 1px solid #0f766e;
        border-radius: 999px;
        background: #f0fdfa;
        color: #0f766e;
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .action-btn-secondary-export:hover:not(:disabled) {
        background: #ccfbf1;
    }

    .action-btn-secondary-export:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
</style>
