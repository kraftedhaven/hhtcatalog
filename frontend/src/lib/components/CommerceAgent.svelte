<script>
    import {
        commerceAudit,
        commerceApprove,
        commerceApply,
        commerceDashboard,
        commerceHistory,
        commerceImport,
        commerceJob,
        commerceRecommendations,
        commerceStartActiveImport,
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

    let dashboard = null;
    let recommendations = [];
    let history = [];
    let loading = false;
    let message = "";
    let error = "";
    let selected = null;
    let statusFilter = "";
    let riskFilter = "";
    let confidenceFilter = "";
    let categoryFilter = "";
    let pilotSize = "10";
    let selectedIds = [];

    $: categoryOptions = Array.from(
        new Set(
            recommendations
                .map((entry) => String(recommendedCategory(entry) || "").trim())
                .filter(Boolean),
        ),
    ).sort((left, right) => left.localeCompare(right));

    $: visible = recommendations.filter(
        (entry) =>
            (!statusFilter || entry.status === statusFilter) &&
            (!riskFilter || entry.risk === riskFilter) &&
            (!confidenceFilter || entry.confidence === confidenceFilter) &&
            (!categoryFilter || String(recommendedCategory(entry) || "") === categoryFilter),
    );

    $: selectedEntries = recommendations.filter((entry) =>
        selectedIds.includes(entry.recommendationId),
    );

    $: selectedPendingCount = selectedEntries.filter(
        (entry) => entry.status === "Pending",
    ).length;

    function fieldLabel(field) {
        return FIELD_LABELS[field] || field;
    }

    function listingKey(field) {
        return LISTING_FIELD_MAP[field] || field;
    }

    function currentValue(entry, field) {
        const value = entry?.listing?.[listingKey(field)];
        return value === undefined || value === null || value === ""
            ? "Not set"
            : String(value);
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
        if (entry?.soldPricing?.status === "ok") return "Sold comparables";
        return "Active listing estimate only";
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
            return;
        }
        if (selectedIds.length >= 20) {
            error = "";
            message = "Pilot selection is limited to 20 listings at a time.";
            return;
        }
        selectedIds = [...selectedIds, id];
    }

    function selectPilotBatch() {
        if (visible.length < 10) {
            error = "At least 10 filtered listings are required to create a pilot batch.";
            return;
        }
        error = "";
        const count = Math.min(20, Math.max(10, Number(pilotSize) || 10));
        const pilotIds = visible
            .slice(0, count)
            .map((entry) => entry.recommendationId);
        selectedIds = pilotIds;
        message = `Selected ${pilotIds.length} listing${pilotIds.length === 1 ? "" : "s"} for the pilot review set.`;
    }

    function clearPilotSelection() {
        selectedIds = [];
    }

    function exportPilotResults() {
        if (selectedEntries.length < 10 || selectedEntries.length > 20) {
            error = "Select 10–20 listings before exporting pilot results.";
            return;
        }
        error = "";
        downloadJSON(
            {
                exportedAt: new Date().toISOString(),
                pilotSize: selectedEntries.length,
                filters: {
                    status: statusFilter,
                    risk: riskFilter,
                    confidence: confidenceFilter,
                    category: categoryFilter,
                },
                recommendations: selectedEntries,
            },
            `commerce-agent-pilot-${new Date().toISOString().slice(0, 10)}.json`,
        );
        message = `Exported ${selectedEntries.length} pilot result${selectedEntries.length === 1 ? "" : "s"}.`;
    }

    async function refresh() {
        loading = true;
        error = "";
        try {
            [dashboard, recommendations, history] = await Promise.all([
                commerceDashboard(),
                commerceRecommendations(),
                commerceHistory(),
            ]);
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    async function importAndAudit() {
        loading = true;
        error = "";
        message = "Importing existing eBay listings...";
        try {
            const imported = await commerceImport();
            const audited = await commerceAudit();
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
            let job = await commerceJob(started.jobId);
            while (job.status === "running") {
                message =
                    "Importing all active eBay listings. You can leave this page open and refresh later.";
                await new Promise((resolve) => setTimeout(resolve, 2500));
                job = await commerceJob(started.jobId);
            }
            if (job.status !== "completed")
                throw new Error(job.error || "Active listing import failed.");
            const imported = job.result || {};
            const audited = await commerceAudit();
            message = `Imported ${imported.imported} active listing${imported.imported === 1 ? "" : "s"} and created ${audited.count} recommendation${audited.count === 1 ? "" : "s"}.`;
            await refresh();
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

    async function apply(entry) {
        if (
            !window.confirm(
                `Apply the approved eBay update for ${entry.listing.title || entry.listing.sku}? This sends only the already-approved fields to eBay.`,
            )
        ) {
            return;
        }
        loading = true;
        error = "";
        try {
            await commerceApply(entry.actionId);
            message = "Approved changes applied through the eBay update flow.";
            await refresh();
        } catch (err) {
            error = err.message || String(err);
        } finally {
            loading = false;
        }
    }

    refresh();
</script>

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
    {#if error}<div class="notice error">{error}</div>{/if}
    {#if message}<div class="notice info">{message}</div>{/if}
    <div class="notice warn">
        <strong>Pricing warning</strong>
        <p>
            Active prices are not sold prices. Review every pricing recommendation
            against approved sold-comparable evidence before applying any update.
        </p>
    </div>
    <div class="actions commerce-actions">
        <button
            class="primary"
            disabled={loading}
            on:click={importActiveAndAudit}
            >{loading ? "Working..." : "Analyze Active Listings"}</button
        >
        <button disabled={loading} on:click={importAndAudit}
            >Import API Inventory</button
        >
        <button disabled={loading} on:click={refresh}>Refresh Queue</button>
    </div>
    <div class="panel nested-panel filters-panel">
        <div class="filter-grid">
            <label>
                <span>Status</span>
                <select bind:value={statusFilter} aria-label="Filter by status">
                    <option value="">All statuses</option>
                    <option value="Pending">Pending</option>
                    <option value="Approved">Approved</option>
                    <option value="Applied">Applied</option>
                    <option value="Failed">Failed</option>
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
        <div class="pilot-toolbar">
            <div>
                <strong>Pilot workflow</strong>
                <p class="help">
                    Review a pilot set of 10–20 listings before expanding to the full catalog.
                </p>
            </div>
            <div class="actions">
                <label class="pilot-size">
                    <span>Pilot size</span>
                    <select bind:value={pilotSize} aria-label="Pilot size">
                        {#each PILOT_SIZES as size}
                            <option value={size}>{size} listings</option>
                        {/each}
                    </select>
                </label>
                <button disabled={loading || visible.length < 10} on:click={selectPilotBatch}
                    >Select first {pilotSize}</button
                >
                <button disabled={!selectedIds.length} on:click={clearPilotSelection}
                    >Clear selection</button
                >
                <button
                    disabled={selectedIds.length < 10 || selectedIds.length > 20}
                    on:click={exportPilotResults}
                    >Export pilot results</button
                >
            </div>
        </div>
        <p class="help workflow-note">
            Pending → <b>Approve only</b> · Approved → <b>Apply approved change</b> · Applied →
            read-only history
        </p>
    </div>
    {#if dashboard}
        <div class="stats commerce-stats">
            <div>
                <strong>{dashboard.listingsFound}</strong><span>Listings found</span>
            </div>
            <div>
                <strong>{dashboard.needOptimization}</strong><span>Need optimization</span>
            </div>
            <div>
                <strong>{dashboard.titleImprovements}</strong><span>Title opportunities</span>
            </div>
            <div>
                <strong>{dashboard.missingItemSpecifics}</strong><span>Missing specifics</span>
            </div>
        </div>
        {#if dashboard.recovery}<div class="help">
                Recovery tracking: {dashboard.recovery.pendingReviews} pending reviews
                · {dashboard.recovery.highRiskPending} high-risk · {dashboard.recovery.appliedChanges} applied
                · {dashboard.recovery.coverage}% catalog coverage
            </div>{/if}
    {/if}
    <div class="commerce-grid">
        <div class="panel nested-panel">
            <div class="section-head">
                <h3>Approval queue</h3>
                <span>{visible.length} shown · {selectedIds.length} in pilot set</span>
            </div>
            {#if !visible.length}<p class="empty">
                    Import your existing eBay listings to create the first audit queue.
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
                        <div class="comparison-table" role="table" aria-label="Current and proposed changes">
                            <div class="comparison-header">Attribute</div>
                            <div class="comparison-header">Current listing</div>
                            <div class="comparison-header">Proposed change</div>
                            {#each proposedEntries(entry) as change}
                                <div class="comparison-cell comparison-attribute">
                                    {fieldLabel(change[0])}
                                </div>
                                <div class="comparison-cell">{currentValue(entry, change[0])}</div>
                                <div class="comparison-cell comparison-proposed">{String(change[1])}</div>
                            {/each}
                        </div>
                    {:else}<p class="help">
                            No field changes recommended from the available evidence.
                        </p>{/if}
                    {#if entry.evidence?.length}
                        <div class="evidence-table" role="table" aria-label="Attribute evidence">
                            <div class="comparison-header">Attribute</div>
                            <div class="comparison-header">Value</div>
                            <div class="comparison-header">Confidence / source</div>
                            <div class="comparison-header">Evidence</div>
                            {#each entry.evidence as evidence}
                                <div class="comparison-cell comparison-attribute">
                                    {fieldLabel(evidence.field)}
                                </div>
                                <div class="comparison-cell">{evidence.value}</div>
                                <div class="comparison-cell">
                                    {evidence.confidence} · {evidence.source}
                                </div>
                                <div class="comparison-cell">{evidence.evidence}</div>
                            {/each}
                        </div>
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
                        {#if entry.status === "Pending"}<button
                                class="primary"
                                disabled={loading}
                                on:click={() => approve(entry)}
                                >Approve only</button
                            >{/if}
                        {#if entry.status === "Approved"}<button
                                class="primary"
                                disabled={loading}
                                on:click={() => apply(entry)}
                                >Apply approved change</button
                            >{/if}
                        {#if entry.status === "Applied"}<span class="help status-readonly"
                                >Applied — read only</span
                            >{/if}
                    </div>
                </article>
            {/each}
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
                    <p>{selected.reason}</p>
                    <p>
                        <b>Confidence:</b> {selected.confidence}. <b>Risk:</b> {selected.risk}.
                    </p>
                    <ul>
                        {#each selected.findings as finding}
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
            </div>
        {/each}
    </div>
</section>
