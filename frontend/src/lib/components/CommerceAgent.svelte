<script>
    import { tick } from "svelte";

    import {
        commerceApprove,
        commerceBulkApprove,
        commerceApply,
        commerceDecision,
        commerceDashboard,
        commerceExplain,
        commerceHistory,
        commerceImport,
        commerceJob,
        commerceRecommendationsPage,
        commerceStartAudit,
        commerceStartActiveImport,
        commerceStartEnrichment,
        commerceStartFullEnrichment,
        commerceRollback,
        downloadJSON,
    } from "$lib/api";

    const FIELD_LABELS = {
        title: "Title",
        price: "Price",
        cat: "Category",
        cid: "Condition ID",
        cnote: "Condition note",
        brand: "Brand",
        model: "Model",
        material: "Material",
        mat: "Material",
        madeIn: "Made in",
        style: "Style",
        theme: "Theme",
        size: "Size",
        color: "Color",
        type: "Type",
        notes: "Seller notes",
    };

    const LISTING_FIELD_MAP = { material: "mat" };
    const PILOT_SIZES = Array.from({ length: 11 }, (_, index) => String(index + 10));
    const QUEUE_PAGE_SIZE = 25;

    let dashboard = null;
    let recommendations = [];
    let history = [];
    let loading = false;
    let message = "";
    let error = "";
    let selected = null;
    let queuePage = 1;
    let queueMeta = { page: 1, pageSize: QUEUE_PAGE_SIZE, total: 0, totalPages: 1 };
    let statusFilter = "";
    let classificationFilter = "";
    let riskFilter = "";
    let confidenceFilter = "";
    let categoryFilter = "";
    let pilotSize = "10";
    let selectedIds = [];
    let pendingApply = null;
    let cancelApplyButton;
    let confirmApplyButton;
    let lastFocusedElement = null;
    let queueHeading;
    let modalCard;

    $: categoryOptions = Array.from(
        new Set(
            recommendations
                .map((entry) => String(recommendedCategory(entry) || "").trim())
                .filter(Boolean),
        ),
    ).sort((left, right) => left.localeCompare(right));
    $: classificationOptions = Array.from(
        new Set(recommendations.map((entry) => entry.classification).filter(Boolean)),
    ).sort((left, right) => left.localeCompare(right));

    $: visible = recommendations.filter(
        (entry) =>
            (!statusFilter || entry.status === statusFilter) &&
            (!classificationFilter ||
                entry.classification === classificationFilter) &&
            (!riskFilter || entry.risk === riskFilter) &&
            (!confidenceFilter || entry.confidence === confidenceFilter) &&
            (!categoryFilter || String(recommendedCategory(entry) || "") === categoryFilter),
    );

    $: selectedIdSet = new Set(selectedIds);
    $: selectedEntries = recommendations.filter((entry) =>
        selectedIdSet.has(entry.recommendationId),
    );
    $: selectedVisibleEntries = visible.filter((entry) =>
        selectedIdSet.has(entry.recommendationId),
    );

    $: selectedPendingCount = selectedEntries.filter(
        (entry) => entry.status === "Pending",
    ).length;

    function displayText(value, placeholder = "Not set") {
        return value === undefined || value === null || value === ""
            ? placeholder
            : String(value);
    }

    function fieldLabel(field) {
        return FIELD_LABELS[field] || field;
    }

    function listingKey(field) {
        return LISTING_FIELD_MAP[field] || field;
    }

    function currentValue(entry, field) {
        return displayText(entry?.listing?.[listingKey(field)]);
    }

    function proposedEntries(entry) {
        return Object.entries(entry?.proposed || {});
    }

    function recommendedCategory(entry) {
        return (
            entry?.proposed?.cat ||
            entry?.taxonomy?.categoryId ||
            entry?.listing?.cat ||
            ""
        );
    }

    function pricingSource(entry) {
        const source = entry?.soldPricing?.pricingSource;
        if (source === "exact_used_sold") return "Exact used sold comparables";
        if (source === "similar_used_sold") return "Similar used sold comparables";
        if (source === "active_comparable") return "Active listing estimate only";
        if (source === "ai_estimate") return "AI estimate only";
        if (source === "seller_price_fallback") return "Seller price retained — no market evidence";
        return "No price evidence available";
    }

    function demandSummary(entry) {
        const score = entry?.demand?.score;
        if (score === undefined || score === null || score === "") {
            return "Not available";
        }
        return `${score}/100`;
    }

    function taxonomySummary(entry) {
        const category = recommendedCategory(entry);
        return category || "Seller review required";
    }

    function toggleSelection(entry) {
        const id = entry.recommendationId;
        if (selectedIds.includes(id)) {
            selectedIds = selectedIds.filter((value) => value !== id);
            error = "";
            message = "";
            return;
        }
        if (selectedIds.length >= 20) {
            message = "";
            error = "Pilot selection is limited to 20 listings at a time.";
            return;
        }
        selectedIds = [...selectedIds, id];
        error = "";
        message = "";
    }

    function selectPilotBatch() {
        if (visible.length < 10) {
            error = "At least 10 filtered listings are required to create a pilot batch.";
            return;
        }
        error = "";
        const count = Math.min(20, Math.max(10, Number(pilotSize) || 10));
        if (visible.length < count) {
            error = `The current filters show ${visible.length} listings. Reduce the pilot size or broaden the filters to select ${count}.`;
            return;
        }
        const pilotIds = visible
            .slice(0, count)
            .map((entry) => entry.recommendationId);
        selectedIds = pilotIds;
        message = `Selected ${pilotIds.length} listing${pilotIds.length === 1 ? "" : "s"} for the pilot review set.`;
    }

    function clearPilotSelection() {
        selectedIds = [];
        error = "";
        message = "";
    }

    function exportPilotResults() {
        if (selectedEntries.length < 10 || selectedEntries.length > 20) {
            message = "";
            error = "Select 10–20 listings before exporting pilot results.";
            return;
        }
        error = "";
        const exportedAt = new Date().toISOString();
        downloadJSON(
            {
                exportedAt,
                pilotSize: selectedEntries.length,
                viewFilters: {
                    status: statusFilter,
                    classification: classificationFilter,
                    risk: riskFilter,
                    confidence: confidenceFilter,
                    category: categoryFilter,
                },
                recommendations: selectedEntries,
            },
            `commerce-agent-pilot-${exportedAt.slice(0, 10)}.json`,
        );
        message = `Exported ${selectedEntries.length} pilot result${selectedEntries.length === 1 ? "" : "s"}.`;
    }

    async function refresh(options = {}) {
        loading = true;
        error = "";
        try {
            const requestedPage = Math.max(1, Number(options.page ?? queuePage) || 1);
            const [nextDashboard, nextQueue, nextHistory] = await Promise.all([
                commerceDashboard(),
                commerceRecommendationsPage(statusFilter, requestedPage, QUEUE_PAGE_SIZE),
                commerceHistory(),
            ]);
            dashboard = nextDashboard;
            queueMeta = nextQueue;
            queuePage = nextQueue.page;
            recommendations = nextQueue.items || [];
            history = nextHistory;
            selectedIds = selectedIds.filter((id) =>
                recommendations.some((entry) => entry.recommendationId === id),
            );
        } catch (err) {
            error = err.message || String(err);
            if (options.throwOnError) throw err;
        } finally {
            loading = false;
        }
    }

    async function setQueuePage(page) {
        const nextPage = Math.min(
            Math.max(1, Number(page) || 1),
            queueMeta.totalPages || 1,
        );
        if (nextPage === queuePage && recommendations.length) return;
        selected = null;
        selectedIds = [];
        await refresh({ page: nextPage });
    }

    async function refreshForStatus() {
        selected = null;
        selectedIds = [];
        await refresh({ page: 1 });
    }

    async function waitForJob(jobId, inProgressMessage) {
        let job = await commerceJob(jobId);
        while (job.status === "queued" || job.status === "running") {
            message = inProgressMessage;
            await new Promise((resolve) => setTimeout(resolve, 2500));
            job = await commerceJob(jobId);
        }
        if (job.status !== "completed") {
            throw new Error(job.error || "Commerce Agent job failed.");
        }
        return job.result || {};
    }

    async function auditInBackground() {
        const started = await commerceStartAudit();
        return waitForJob(
            started.jobId,
            "Auditing local catalog evidence and recommendations. No eBay changes are being made.",
        );
    }

    async function importAndAudit() {
        loading = true;
        error = "";
        message = "Importing existing eBay listings...";
        try {
            const imported = await commerceImport();
            const audited = await auditInBackground();
            message = `Imported ${imported.imported} listing${imported.imported === 1 ? "" : "s"} and created ${audited.count} recommendation${audited.count === 1 ? "" : "s"}.`;
            await refresh();
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function importActiveAndAudit() {
        loading = true;
        error = "";
        message = "Importing all active eBay listings...";
        try {
            const started = await commerceStartActiveImport();
            const imported = await waitForJob(
                started.jobId,
                "Importing all active eBay listings. You can leave this page open and refresh later.",
            );
            const audited = await auditInBackground();
            message = `Imported ${imported.imported} active listing${imported.imported === 1 ? "" : "s"} and created ${audited.count} recommendation${audited.count === 1 ? "" : "s"}.`;
            await refresh();
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function enrichSelectedPilot() {
        const listingIds = selectedEntries
            .map((entry) => String(entry?.listing?.listingId || "").trim())
            .filter(Boolean);
        if (!listingIds.length) {
            error = "The selected records do not have active eBay listing IDs to enrich.";
            return;
        }
        loading = true;
        error = "";
        message = "Starting read-only eBay detail enrichment for the selected pilot...";
        try {
            const started = await commerceStartEnrichment(listingIds);
            const enriched = await waitForJob(
                started.jobId,
                "Fetching official eBay details and item specifics. No eBay changes are being made.",
            );
            const audited = await auditInBackground();
            message = `Read-only enrichment completed for ${enriched.updated || 0} of ${enriched.requested || listingIds.length} selected listing${listingIds.length === 1 ? "" : "s"}; refreshed ${audited.count || 0} recommendations.`;
            await refresh();
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function enrichFullCatalog(resumeFailed = false) {
        loading = true;
        error = "";
        message = resumeFailed
            ? "Resuming failed read-only eBay detail checks in 20-listing chunks..."
            : "Starting resumable read-only enrichment for active eBay listings in 20-listing chunks...";
        try {
            const started = await commerceStartFullEnrichment(resumeFailed);
            const result = await waitForJob(
                started.jobId,
                "Fetching official eBay details with saved checkpoints. No eBay changes are being made.",
            );
            const checkpoints = result.checkpoints || {};
            message = `Read-only enrichment completed: ${checkpoints.processed || 0} processed, ${checkpoints.failed || 0} failed. The approval queue was refreshed without changing eBay.`;
            await refresh({ page: 1 });
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function approve(entry) {
        loading = true;
        error = "";
        try {
            await commerceApprove(entry.recommendationId, entry.proposed);
            message = `Approval saved for ${entry.listing.title || entry.listing.sku}. Nothing has been sent to eBay yet.`;
            selected = null;
            await refresh();
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function explain(entry) {
        loading = true;
        error = "";
        try {
            const result = await commerceExplain(entry.recommendationId);
            selected = { ...entry, explanation: result };
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function decide(entry, decision) {
        loading = true;
        error = "";
        try {
            await commerceDecision(entry.recommendationId, decision);
            message = `${decision === "reject" ? "Rejected" : "Skipped"} recommendation for ${entry.listing.title || entry.listing.sku}.`;
            selected = null;
            await refresh({ throwOnError: true });
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function approveSelected() {
        const ids = selectedEntries
            .filter((entry) => entry.status === "Pending" && entry.risk !== "high" && proposedEntries(entry).length)
            .map((entry) => entry.recommendationId);
        if (!ids.length) {
            error = "Select pending low-risk recommendations with proposed fields first.";
            return;
        }
        loading = true;
        error = "";
        try {
            const result = await commerceBulkApprove(ids);
            message = `Approved ${result.approved || 0} of ${ids.length} selected recommendations. Nothing was sent to eBay.`;
            await refresh({ throwOnError: true });
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function rollback(entry) {
        loading = true;
        error = "";
        try {
            await commerceRollback(entry.actionId);
            message = "Rollback completed after eBay state verification.";
            await refresh({ throwOnError: true });
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function requestApply(entry) {
        error = "";
        lastFocusedElement = document.activeElement;
        pendingApply = entry;
        await tick();
        cancelApplyButton?.focus();
    }

    async function restoreFocus() {
        await tick();
        if (lastFocusedElement?.isConnected) {
            lastFocusedElement.focus();
            return;
        }
        queueHeading?.focus();
    }

    async function focusQueueHeading() {
        await tick();
        queueHeading?.focus();
    }

    async function cancelApply() {
        pendingApply = null;
        cancelApplyButton = null;
        confirmApplyButton = null;
        error = "";
        await restoreFocus();
    }

    async function confirmApply() {
        if (!pendingApply) return;
        const latestEntry = recommendations.find(
            (entry) =>
                entry.recommendationId === pendingApply.recommendationId,
        );
        if (latestEntry?.status !== "Approved" || !latestEntry?.actionId) {
            error =
                "This recommendation is no longer ready to apply. Refresh the queue and reopen the approved change.";
            pendingApply = null;
            cancelApplyButton = null;
            confirmApplyButton = null;
            await focusQueueHeading();
            return;
        }
        loading = true;
        error = "";
        await tick();
        modalCard?.focus();
        let applied = false;
        try {
            await commerceApply(latestEntry.actionId);
            applied = true;
            message = "Approved changes applied through the eBay update flow.";
            await refresh({ throwOnError: true });
            pendingApply = null;
            cancelApplyButton = null;
            confirmApplyButton = null;
            await focusQueueHeading();
        } catch (err) {
            if (applied) {
                pendingApply = null;
                cancelApplyButton = null;
                confirmApplyButton = null;
                message =
                    "Approved changes were applied through the eBay update flow, but refreshing the queue failed. Refresh the queue to see the latest status.";
                error = err.message || String(err);
                await focusQueueHeading();
            } else {
                error = err.message || String(err);
                await tick();
                confirmApplyButton?.focus();
            }
        } finally {
            loading = false;
        }
    }

    function handleDialogKeydown(event) {
        if (!pendingApply) return;
        if (
            event.target instanceof Node &&
            !modalCard?.contains(event.target)
        ) {
            return;
        }
        if (event.key === "Escape") {
            event.preventDefault();
            void cancelApply();
            return;
        }
        if (event.key !== "Tab") return;
        const focusable = [cancelApplyButton, confirmApplyButton].filter(Boolean);
        if (!focusable.length) return;
        const currentIndex = focusable.indexOf(document.activeElement);
        if (event.shiftKey) {
            if (currentIndex <= 0) {
                event.preventDefault();
                focusable[focusable.length - 1]?.focus();
            }
            return;
        }
        if (currentIndex === -1 || currentIndex === focusable.length - 1) {
            event.preventDefault();
            focusable[0]?.focus();
        }
    }

    function handleBackdropClick(event) {
        if (event.target === event.currentTarget) {
            void cancelApply();
        }
    }

    refresh();
</script>

<svelte:window on:keydown={handleDialogKeydown} />

<section class="commerce-agent">
    <div class="commerce-head">
        <div>
            <p class="eyebrow">COMMERCE AGENT</p>
            <h2>What needs your attention?</h2>
            <p class="help">
                eBay listings are imported from official APIs. Recommendations
                are reviewed before any approved fields are sent back to eBay.
            </p>
        </div>
        <span class="mode-badge">Recommend only</span>
    </div>
    {#if error}<div class="notice error" role="alert">{error}</div>{/if}
    {#if message}<div class="notice info" aria-live="polite">{message}</div>{/if}
    <div class="agent-next-step panel">
        <div>
            <span class="step-label">NEXT STEP</span>
            <h3>{dashboard?.missingItemSpecifics ? `Review ${dashboard.missingItemSpecifics} missing item specifics` : "Review the recommendations below"}</h3>
            <p class="help">Start with the first card. Open the details only when you need to verify evidence.</p>
        </div>
        <button class="primary" disabled={loading} on:click={refresh}>Refresh recommendations</button>
    </div>
    <details class="panel advanced-panel">
        <summary>Import, enrich, and pilot tools</summary>
        <p class="help">These tools are safe to run, but are not needed for everyday review.</p>
        <div class="actions commerce-actions">
            <button class="primary" disabled={loading} on:click={importActiveAndAudit}>{loading ? "Working..." : "Analyze Active Listings"}</button>
            <button disabled={loading} on:click={importAndAudit}>Import API Inventory</button>
            <button disabled={loading} on:click={() => enrichFullCatalog(false)}>Enrich active catalog (read-only)</button>
            <button disabled={loading} on:click={() => enrichFullCatalog(true)}>Resume failed enrichment</button>
        </div>
        <div class="pilot-toolbar">
            <div><strong>Pilot workflow</strong><p class="help">Review 10–20 listings before expanding to the full catalog.</p></div>
            <div class="actions">
                <label class="pilot-size"><span>Pilot size</span><select bind:value={pilotSize} aria-label="Pilot size">{#each PILOT_SIZES as size}<option value={size}>{size} listings</option>{/each}</select></label>
                <button disabled={loading || visible.length < 10} on:click={selectPilotBatch}>Select first {pilotSize}</button>
                <button disabled={!selectedIds.length} on:click={clearPilotSelection}>Clear selection</button>
                <button disabled={selectedEntries.length < 10 || selectedEntries.length > 20} on:click={exportPilotResults}>Export pilot results</button>
                <button class="primary" disabled={loading || !selectedEntries.some((entry) => entry?.listing?.listingId)} on:click={enrichSelectedPilot}>Enrich selected (read-only)</button>
                <button disabled={loading || !selectedPendingCount} on:click={approveSelected}>Approve selected (no eBay write)</button>
            </div>
        </div>
    </details>
    <details class="panel filters-panel">
        <summary>Filter recommendations</summary>
        <div class="filter-grid">
            <label>
                <span>Status</span>
                <select bind:value={statusFilter} aria-label="Filter by status" on:change={refreshForStatus}>
                    <option value="">All statuses</option>
                    <option value="Pending">Pending</option>
                    <option value="Approved">Approved</option>
                    <option value="Applied">Applied</option>
                    <option value="Failed">Failed</option>
                </select>
            </label>
            <label>
                <span>Type</span>
                <select bind:value={classificationFilter} aria-label="Filter by recommendation type">
                    <option value="">All recommendation types</option>
                    {#each classificationOptions as classification}
                        <option value={classification}>{classification}</option>
                    {/each}
                </select>
            </label>
            <label>
                <span>Risk</span>
                <select bind:value={riskFilter} aria-label="Filter by risk">
                    <option value="">All risk levels</option>
                    <option value="low">Low risk</option>
                    <option value="high">High risk</option>
                </select>
            </label>
            <label>
                <span>Confidence</span>
                <select bind:value={confidenceFilter} aria-label="Filter by confidence">
                    <option value="">All confidence levels</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                </select>
            </label>
            <label>
                <span>Category</span>
                <select bind:value={categoryFilter} aria-label="Filter by category">
                    <option value="">All categories</option>
                    {#each categoryOptions as category}
                        <option value={category}>{category}</option>
                    {/each}
                </select>
            </label>
        </div>
    </details>
    {#if dashboard}
        <div class="attention-summary" aria-label="Catalog attention summary">
            <div><strong>{dashboard.listingsFound}</strong><span>active listings</span></div>
            <div class:attention={dashboard.missingItemSpecifics > 0}><strong>{dashboard.missingItemSpecifics}</strong><span>need item specifics</span></div>
            <div><strong>{dashboard.titleImprovements || 0}</strong><span>title changes</span></div>
            <div><strong>{dashboard.recovery?.appliedChanges || 0}</strong><span>changes applied</span></div>
        </div>
        <p class="help summary-note">Recommendations are suggestions, not automatic changes. Nothing is sent to eBay until you approve it.</p>
    {/if}
    <div class="commerce-grid">
        <div class="panel nested-panel">
            <div class="section-head">
                <h3 bind:this={queueHeading} tabindex="-1">Approval queue</h3>
                <span>Page {queueMeta.page} of {queueMeta.totalPages} · {queueMeta.total} total · {visible.length} shown</span>
            </div>
            <div class="queue-pagination" aria-label="Approval queue pages">
                <button disabled={loading || queuePage <= 1} on:click={() => setQueuePage(queuePage - 1)}
                    >Previous</button
                >
                <span>Page {queueMeta.page} of {queueMeta.totalPages}</span>
                <button disabled={loading || queuePage >= queueMeta.totalPages} on:click={() => setQueuePage(queuePage + 1)}
                    >Next</button
                >
            </div>
            {#if !visible.length}<p class="empty">
                    No recommendations match this page and filter combination.
                </p>{/if}
            {#each visible as entry}
                <article class="recommendation-card">
                    <div class="recommendation-title">
                        <label class="pilot-select">
                            <input
                                type="checkbox"
                                checked={selectedIds.includes(entry.recommendationId)}
                                disabled={!selectedIds.includes(entry.recommendationId) && selectedIds.length >= 20}
                                on:change={() => toggleSelection(entry)}
                            />
                            <span>Select for pilot</span>
                        </label>
                        <div class="recommendation-heading">
                            <strong>{entry.listing.title || "Untitled listing"}</strong>
                            <span>
                                {entry.classification} · Score {entry.score}/100 · {entry.status}
                            </span>
                        </div>
                        <span class="risk">{entry.risk} risk</span>
                    </div>
                    <div class="listing-meta">
                        <b>{entry.listing.lifecycle || "Inventory record"}</b>
                        <span>SKU: {entry.listing.sku || "not provided"}</span>
                        <span>Offer: {entry.listing.offerId || "none"}</span>
                        <span>Listing: {entry.listing.listingId || "none"}</span>
                        {#if entry.listing.enrichedAt}<span>Official details enriched: {new Date(entry.listing.enrichedAt).toLocaleDateString()}</span>{/if}
                        {#if entry.listing.ebayUrl}<a
                                href={entry.listing.ebayUrl}
                                target="_blank"
                                rel="noreferrer">Open on eBay</a
                            >{/if}
                    </div>
                    <p>{entry.reason}</p>
                    <div class="advisory-grid">
                        <div>
                            <span>Title candidate</span>
                            <strong>{entry.proposed.title || "No title change proposed"}</strong>
                        </div>
                        <div>
                            <span>Category recommendation</span>
                            <strong>{taxonomySummary(entry)}</strong>
                        </div>
                        <div>
                            <span>Pricing source</span>
                            <strong>{pricingSource(entry)}</strong>
                        </div>
                        <div>
                            <span>Demand score</span>
                            <strong>{demandSummary(entry)}</strong>
                        </div>
                        <div>
                            <span>Confidence</span>
                            <strong>{entry.confidence}</strong>
                        </div>
                        <div>
                            <span>Risk</span>
                            <strong>{entry.risk}</strong>
                        </div>
                    </div>
                    {#if proposedEntries(entry).length}
                        <details class="card-details">
                            <summary>Show proposed changes ({proposedEntries(entry).length})</summary>
                        <div class="table-scroll">
                            <table class="comparison-table" aria-label="Current and proposed changes">
                                <thead>
                                    <tr>
                                        <th scope="col">Attribute</th>
                                        <th scope="col">Current listing</th>
                                        <th scope="col">Proposed change</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {#each proposedEntries(entry) as change}
                                        <tr>
                                            <th scope="row" class="comparison-attribute">
                                                {fieldLabel(change[0])}
                                            </th>
                                            <td>{currentValue(entry, change[0])}</td>
                                            <td class="comparison-proposed">{String(change[1])}</td>
                                        </tr>
                                    {/each}
                                </tbody>
                            </table>
                        </div>
                        </details>
                    {:else}<p class="help">
                            No field changes recommended from the available evidence.
                        </p>{/if}
                    {#if entry.evidence?.length}
                        <details class="card-details">
                            <summary>Show evidence ({entry.evidence.length} fields)</summary>
                        <div class="table-scroll">
                            <table class="evidence-table" aria-label="Attribute evidence">
                                <thead>
                                    <tr>
                                        <th scope="col">Attribute</th>
                                        <th scope="col">Value</th>
                                        <th scope="col">Confidence / source</th>
                                        <th scope="col">Evidence</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {#each entry.evidence as evidence}
                                        <tr>
                                            <th scope="row" class="comparison-attribute">
                                                {fieldLabel(evidence.field)}
                                            </th>
                                            <td>{displayText(evidence.value, "Not provided")}</td>
                                            <td>
                                                {displayText(evidence.confidence, "Unknown")} · {displayText(evidence.source, "Unknown")}
                                            </td>
                                            <td>{displayText(evidence.evidence, "No evidence text provided")}</td>
                                        </tr>
                                    {/each}
                                </tbody>
                            </table>
                        </div>
                        </details>
                    {/if}
                    {#if entry.taxonomy}<div class="help meta-note">
                            <b>Taxonomy:</b> {entry.taxonomy.message}
                        </div>{/if}
                    {#if entry.soldPricing}<div class="help meta-note">
                            <b>Pricing:</b> {entry.soldPricing.message}
                        </div>{/if}
                    {#if entry.demand}<div class="help meta-note">
                            <b>Demand proxy:</b> {entry.demand.message}
                        </div>{/if}
                    <div class="actions">
                        <button on:click={() => (selected = entry)}>Review details</button>
                        <button disabled={loading} on:click={() => explain(entry)}>Explain</button>
                        {#if entry.status === "Pending" && entry.risk !== "high" && proposedEntries(entry).length}<button
                                class="primary"
                                disabled={loading}
                                on:click={() => approve(entry)}
                                >Approve only</button
                            >{/if}
                        {#if entry.status === "Pending"}<button disabled={loading} on:click={() => decide(entry, "skip")}>Skip</button>
                            <button disabled={loading} on:click={() => decide(entry, "reject")}>Reject</button>{/if}
                        {#if entry.status === "Pending" && entry.risk === "high"}<span class="help status-readonly"
                                >High risk — seller review only</span
                            >{/if}
                        {#if entry.status === "Pending" && entry.risk !== "high" && !proposedEntries(entry).length}<span class="help status-readonly"
                                >No safe field change proposed</span
                            >{/if}
                        {#if entry.status === "Approved"}<button
                                class="primary"
                                disabled={loading}
                                on:click={() => requestApply(entry)}
                                >Apply approved change</button
                            >{/if}
                        {#if entry.status === "Applied"}<span class="help status-readonly"
                                >Applied — read only</span
                            >{/if}
                    </div>
                </article>
            {/each}
            {#if visible.length}
                <div class="queue-pagination queue-pagination-bottom" aria-label="Approval queue pages">
                    <button disabled={loading || queuePage <= 1} on:click={() => setQueuePage(queuePage - 1)}
                        >Previous</button
                    >
                    <span>Page {queueMeta.page} of {queueMeta.totalPages}</span>
                    <button disabled={loading || queuePage >= queueMeta.totalPages} on:click={() => setQueuePage(queuePage + 1)}
                        >Next</button
                    >
                </div>
            {/if}
        </div>
        <aside class="panel nested-panel explanation">
            <h3>Pilot review</h3>
            <p class="help">
                Use the pilot set to review recommendations before expanding to the full catalog.
            </p>
            <div class="pilot-summary-grid">
                <div>
                    <strong>{selectedIds.length}</strong>
                    <span>Selected</span>
                </div>
                <div>
                    <strong>{selectedVisibleEntries.length}</strong>
                    <span>Visible in filters</span>
                </div>
                <div>
                    <strong>{selectedPendingCount}</strong>
                    <span>Pending approval</span>
                </div>
                <div>
                    <strong>{selectedEntries.filter((entry) => entry.risk === "high").length}</strong>
                    <span>High risk</span>
                </div>
                <div>
                    <strong>{selectedEntries.filter((entry) => entry.status === "Applied").length}</strong>
                    <span>Applied</span>
                </div>
            </div>
            {#if selected}
                <div class="detail-block">
                    <h4>{selected.listing.title || selected.listing.sku}</h4>
                    <p>{selected.explanation?.reason || selected.reason}</p>
                    {#if selected.explanation}<p class="help">Loaded from stored evidence and rationale; no new facts were generated.</p>{/if}
                    <p>
                        <b>Confidence:</b> {selected.confidence}. <b>Risk:</b> {selected.risk}.
                    </p>
                    <ul>
                        {#each selected.findings || [] as finding}
                            <li><b>{fieldLabel(finding.field)}:</b> {finding.message}</li>
                        {/each}
                    </ul>
                    <button on:click={() => (selected = null)}>Close</button>
                </div>
            {:else}
                <p class="empty">Select “Review details” on a recommendation for more context.</p>
            {/if}
        </aside>
    </div>
    <div class="panel nested-panel history-panel">
        <h3>Change history</h3>
        {#if !history.length}<p class="empty">No approved changes yet.</p>{/if}
        {#each history.slice(0, 10) as entry}
            <div class="history-row">
                <strong>{entry.listing.title || entry.listing.sku}</strong>
                <span>{entry.status} · {new Date(entry.createdAt).toLocaleString()}</span>
                {#if entry.rollbackEligible}<button disabled={loading} on:click={() => rollback(entry)}>Rollback safely</button>{/if}
            </div>
        {/each}
    </div>
    {#if pendingApply}
        <div class="modal-backdrop" role="presentation" on:click={handleBackdropClick}>
            <div
                class="modal-card"
                bind:this={modalCard}
                role="dialog"
                aria-modal="true"
                aria-labelledby="apply-dialog-title"
                aria-describedby="apply-dialog-description"
                tabindex="-1"
            >
                <h3 id="apply-dialog-title">Confirm eBay update</h3>
                <p>
                    Apply the approved eBay update for
                    <b>{pendingApply.listing.title || pendingApply.listing.sku}</b>?
                </p>
                <p id="apply-dialog-description" class="help">
                    This sends only the already-approved fields to eBay. Reviewing or approving a
                    recommendation does not apply it.
                </p>
                <div class="actions">
                    <button
                        bind:this={cancelApplyButton}
                        disabled={loading}
                        on:click={() => void cancelApply()}
                        >Cancel</button
                    >
                    <button
                        bind:this={confirmApplyButton}
                        class="primary"
                        disabled={loading}
                        on:click={confirmApply}
                        >{loading ? "Applying..." : "Confirm apply"}</button
                    >
                </div>
            </div>
        </div>
    {/if}
</section>
