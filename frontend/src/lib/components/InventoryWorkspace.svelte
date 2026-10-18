<script>
    import { onMount, onDestroy } from "svelte";
    import {
        inventoryCreateBatch,
        inventoryListBatches,
        inventoryGetBatch,
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
        ebayCategorySuggestions,
    } from "$lib/api";
    import {
        formatBytes,
        prepareUploadBatch,
        partitionUploadChunks,
    } from "$lib/image-compressor";

    export let onSwitchToListingOptimization = () => {};
    export let onOpenFullReview = () => {};

    // Workspace modes: 'new_inventory' | 'listing_optimization'
    let activeSubTab = "new_inventory";

    // Batch state
    let batches = [];
    let selectedBatchId = "";
    let selectedBatch = null;
    let batchLoading = false;

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
            const updated = await inventoryEnrichFromResearch(inspectingItem.id, researchClues);
            const index = items.findIndex((item) => item.id === updated.id);
            if (index !== -1) items[index] = updated;
            items = [...items];
            inspectingItem = updated;
            globalStatus = "Research clues saved. Title candidates and price options are ready for review.";
        } catch (err) {
            globalError = err.message || "Could not generate research suggestions.";
        } finally {
            researchLoading = false;
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
        const ids = Array.from(selectedItemIds);
        if (!ids.length) {
            globalError = "Select at least one item to approve.";
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
        const ids = Array.from(selectedItemIds);
        if (!ids.length) {
            globalError = "Select approved items to submit.";
            return;
        }
        // Check if all selected items are approved
        const nonApproved = items.filter(
            (it) => ids.includes(it.id) && it.workflowStatus !== "approved",
        );
        if (nonApproved.length > 0) {
            globalError = `Cannot submit: ${nonApproved.length} selected item(s) are not in 'approved' status. Validate and approve items first.`;
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
            items = await inventoryListBatchItems(selectedBatchId);
            if (inspectingItemId) {
                inspectingItem =
                    items.find((it) => it.id === inspectingItemId) || null;
            }
            globalStatus = `Submitted ${res.submittedCount} draft(s) to eBay Seller Hub! Task ID: ${res.feedTaskId}. Polling results...`;
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
            closeUploadModal();
            globalStatus = `Successfully uploaded ${uploadFiles.length} photo(s) (under 10MB limit) and updated item groups.`;
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

    // Helper to get status badge classes
    function getStatusBadge(status) {
        switch (status) {
            case "ready_for_approval":
            case "approved":
                return {
                    text: "Ready",
                    bg: "bg-emerald-50 text-emerald-700 border-emerald-200",
                };
            case "needs_review":
                return {
                    text: "Review",
                    bg: "bg-slate-100 text-slate-700 border-slate-300",
                };
            case "grouped":
                return {
                    text: "Grouped",
                    bg: "bg-gray-100 text-gray-700 border-gray-200",
                };
            case "photo_staged":
                return {
                    text: "Staged",
                    bg: "bg-indigo-50 text-indigo-700 border-indigo-200",
                };
            case "analysis_queued":
            case "analysis_running":
                return {
                    text: "Analyzing...",
                    bg: "bg-blue-50 text-blue-700 border-blue-200 animate-pulse",
                };
            case "draft_submitted":
                return {
                    text: "Submitted",
                    bg: "bg-cyan-50 text-cyan-700 border-cyan-200",
                };
            case "draft_result_available":
                return {
                    text: "eBay Available",
                    bg: "bg-teal-50 text-teal-700 border-teal-200",
                };
            case "draft_failed":
                return {
                    text: "Failed",
                    bg: "bg-rose-50 text-rose-700 border-rose-200",
                };
            default:
                return {
                    text: status,
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

        <!-- Batch Selector & Creation -->
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
        </div>
    </div>

    <!-- Instruction Help Bar -->
    <div class="instruction-bar">
        <span
            >Edit fields directly. Select a row to review its photos and full
            listing details.</span
        >
        <div class="flex items-center gap-3">
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

    <!-- Main Workspace Container with Spreadsheet Table & Inspector -->
    <div class="spreadsheet-container">
        <!-- Main Spreadsheet Table -->
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
                                    <input
                                        type="text"
                                        class="table-input"
                                        value={listing.cat || ""}
                                        placeholder="Category..."
                                        on:input={(e) =>
                                            handleInlineEdit(
                                                item,
                                                "cat",
                                                e.target.value,
                                            )}
                                    />
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

                                <!-- Price (with Tier options popover) -->
                                <td>
                                    <div
                                        class="relative flex items-center gap-1"
                                    >
                                        <span class="text-slate-400 text-sm"
                                            >$</span
                                        >
                                        <input
                                            type="number"
                                            step="0.01"
                                            class="table-input w-24 text-right"
                                            value={listing.price || ""}
                                            placeholder="0.00"
                                            on:input={(e) =>
                                                handleInlineEdit(
                                                    item,
                                                    "price",
                                                    e.target.value,
                                                )}
                                        />
                                        {#if item.pricingOptions && Object.keys(item.pricingOptions).length > 0}
                                            <button
                                                type="button"
                                                class="price-tiers-btn"
                                                title="View recommended price tiers"
                                                on:click|stopPropagation={() => {
                                                    activePriceTiersItemId =
                                                        activePriceTiersItemId ===
                                                        item.id
                                                            ? null
                                                            : item.id;
                                                }}
                                            >
                                                🏷️
                                            </button>
                                            {#if activePriceTiersItemId === item.id}
                                                <div class="candidate-dropdown">
                                                    <div
                                                        class="p-2 border-b text-xs font-bold text-slate-700"
                                                    >
                                                        Recommended Price Tiers
                                                    </div>
                                                    {#each Object.entries(item.pricingOptions) as [tKey, opt]}
                                                        <button
                                                            type="button"
                                                            class="candidate-option-btn"
                                                            on:click={() =>
                                                                selectPricingOption(
                                                                    item,
                                                                    tKey,
                                                                )}
                                                        >
                                                            <div
                                                                class="flex justify-between items-center text-xs font-bold"
                                                            >
                                                                <span
                                                                    >{opt.label}</span
                                                                >
                                                                <span
                                                                    class="text-emerald-700"
                                                                    >${opt.price}</span
                                                                >
                                                            </div>
                                                            <p
                                                                class="text-xs text-slate-500 mt-0.5"
                                                            >
                                                                {opt.reason}
                                                            </p>
                                                        </button>
                                                    {/each}
                                                </div>
                                            {/if}
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
                                    {#if isMissingSize && item.workflowStatus === "needs_review"}
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

        <!-- Slide-Over Item Inspector Panel -->
        {#if inspectingItem}
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

                    <!-- Seller research clues -->
                    <div class="inspector-section">
                        <h4 class="inspector-section-title">Research clues</h4>
                        <p class="text-xs text-slate-500 mb-3">
                            Paste factual clues from a comparable listing or label. HHT saves them as seller input and generates suggestions; verify every field before approval.
                        </p>
                        <textarea
                            class="w-full text-xs p-2 border rounded-md h-20 text-slate-700 mb-2"
                            placeholder="Paste an observed title or product details..."
                            bind:value={researchClues.observedTitle}
                        ></textarea>
                        <div class="form-grid compact">
                            <label class="field"><span>Brand</span><input bind:value={researchClues.brand} placeholder="e.g. Nike" /></label>
                            <label class="field"><span>Item type</span><input bind:value={researchClues.itemType} placeholder="e.g. football jersey" /></label>
                            <label class="field"><span>Model / number</span><input bind:value={researchClues.model} placeholder="e.g. #12" /></label>
                            <label class="field"><span>Category</span><input bind:value={researchClues.category} placeholder="e.g. sports jersey" /></label>
                            <label class="field"><span>Size</span><input bind:value={researchClues.size} placeholder="e.g. Men's Large" /></label>
                            <label class="field"><span>Comparable price</span><input type="text" bind:value={researchClues.comparablePrice} placeholder="e.g. 54.99" /></label>
                        </div>
                        <button
                            type="button"
                            class="action-btn-primary mt-3"
                            on:click={generateResearchSuggestions}
                            disabled={researchLoading}
                        >
                            {researchLoading ? "Generating..." : "Generate title & price options"}
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

    <!-- Sticky Bottom Action Bar matching Mockup media_1792112229.png -->
    <div class="bottom-action-bar">
        <div class="action-bar-inner">
            <button
                type="button"
                class="action-btn-outline"
                on:click={openUploadModal}
                disabled={actionLoading}
            >
                Upload photos
            </button>

            <button
                type="button"
                class="action-btn-primary"
                on:click={validateSelected}
                disabled={actionLoading || selectedItemIds.size === 0}
            >
                Validate selected drafts ({selectedItemIds.size})
            </button>

            <button
                type="button"
                class="action-btn-approve"
                on:click={approveSelected}
                disabled={actionLoading || selectedItemIds.size === 0}
            >
                Approve valid ({selectedItemIds.size})
            </button>

            <button
                type="button"
                class="action-btn-submit"
                on:click={submitSelectedDrafts}
                disabled={actionLoading || selectedItemIds.size === 0}
            >
                Submit drafts to eBay ({selectedItemIds.size})
            </button>
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
                            Supports JPEG, PNG, WebP, HEIC. Large photos are automatically compressed before upload (max 10MB request).
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
                                            (acc, p) => acc + (p.originalSize || 0),
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
                                    <div class="relative aspect-video rounded overflow-hidden bg-slate-100">
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
                                        <p class="text-[11px] font-medium text-slate-800 truncate" title={prev.name}>
                                            {prev.name}
                                        </p>
                                        <div class="flex items-center justify-between text-[10px] text-slate-500 mt-0.5">
                                            <span>Orig: {prev.formattedOriginalSize}</span>
                                            <span class="font-semibold {prev.uploadSize ? 'text-emerald-700' : 'text-slate-600'}">
                                                {prev.uploadSize ? `Upload: ${prev.formattedUploadSize}` : prev.formattedUploadSize}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            {/each}
                        </div>
                    </div>
                {/if}

                {#if isUploading}
                    <div class="mt-3 p-2.5 rounded-lg bg-sky-50 border border-sky-200 text-xs text-sky-800 flex items-center gap-2">
                        <span class="animate-spin">⏳</span>
                        <span class="font-semibold">{uploadPhase === "compressing" ? "Compressing photos..." : "Uploading photos..."}</span>
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
</style>
