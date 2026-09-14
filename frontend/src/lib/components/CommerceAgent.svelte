<script>
    import { commerceAudit, commerceApprove, commerceApply, commerceDashboard, commerceHistory, commerceImport, commerceRecommendations } from "$lib/api";

    let dashboard = null;
    let recommendations = [];
    let history = [];
    let loading = false;
    let message = "";
    let error = "";
    let selected = null;
    let filter = "";

    $: visible = recommendations.filter((entry) => !filter || entry.classification === filter || entry.status === filter);

    async function refresh() {
        loading = true; error = "";
        try {
            [dashboard, recommendations, history] = await Promise.all([commerceDashboard(), commerceRecommendations(), commerceHistory()]);
        } catch (err) { error = err.message || String(err); }
        finally { loading = false; }
    }

    async function importAndAudit() {
        loading = true; error = ""; message = "Importing existing eBay listings...";
        try {
            const imported = await commerceImport();
            const audited = await commerceAudit();
            message = `Imported ${imported.imported} listing${imported.imported === 1 ? "" : "s"} and created ${audited.count} recommendation${audited.count === 1 ? "" : "s"}.`;
            await refresh();
        } catch (err) { error = err.message || String(err); }
        finally { loading = false; }
    }

    async function approve(entry) {
        loading = true; error = "";
        try {
            const result = await commerceApprove(entry.recommendationId, entry.proposed);
            const applied = await commerceApply(result.actionId);
            message = applied.status === "Applied" ? "Approved changes applied through the existing eBay update flow." : "Approval saved.";
            selected = null;
            await refresh();
        } catch (err) { error = err.message || String(err); }
        finally { loading = false; }
    }

    refresh();
</script>

<section class="commerce-agent">
    <div class="commerce-head">
        <div>
            <p class="eyebrow">COMMERCE AGENT</p>
            <h2>What needs your attention?</h2>
            <p class="help">eBay listings are imported from official APIs. Recommendations are reviewed before any approved fields are sent back to eBay.</p>
        </div>
        <span class="mode-badge">Recommend only</span>
    </div>
    {#if error}<div class="notice error">{error}</div>{/if}
    {#if message}<div class="notice info">{message}</div>{/if}
    <div class="actions commerce-actions">
        <button class="primary" disabled={loading} on:click={importAndAudit}>{loading ? "Working..." : "Analyze My Listings"}</button>
        <button disabled={loading} on:click={refresh}>Refresh Queue</button>
        <select bind:value={filter} aria-label="Filter recommendations">
            <option value="">All recommendations</option>
            <option value="Needs Review">Needs Review</option>
            <option value="High Priority">High Priority</option>
            <option value="Needs Optimization">Needs Optimization</option>
            <option value="Approved">Approved</option>
            <option value="Applied">Applied</option>
        </select>
    </div>
    {#if dashboard}
        <div class="stats commerce-stats">
            <div><strong>{dashboard.listingsFound}</strong><span>Listings found</span></div>
            <div><strong>{dashboard.needOptimization}</strong><span>Need optimization</span></div>
            <div><strong>{dashboard.titleImprovements}</strong><span>Title opportunities</span></div>
            <div><strong>{dashboard.missingItemSpecifics}</strong><span>Missing specifics</span></div>
        </div>
    {/if}
    <div class="commerce-grid">
        <div class="panel nested-panel">
            <h3>Approval queue</h3>
            {#if !visible.length}<p class="empty">Import your existing eBay listings to create the first audit queue.</p>{/if}
            {#each visible as entry}
                <article class="recommendation-card">
                    <div class="recommendation-title"><div><strong>{entry.listing.title || "Untitled listing"}</strong><span>{entry.classification} · Score {entry.score}/100 · {entry.status}</span></div><span class="risk">{entry.risk} risk</span></div>
                    <div class="listing-meta"><b>{entry.listing.lifecycle || "Inventory record"}</b><span>SKU: {entry.listing.sku || "not provided"}</span><span>Offer: {entry.listing.offerId || "none"}</span><span>Listing: {entry.listing.listingId || "none"}</span>{#if entry.listing.ebayUrl}<a href={entry.listing.ebayUrl} target="_blank" rel="noreferrer">Open on eBay</a>{/if}</div>
                    <p>{entry.reason}</p>
                    {#if Object.keys(entry.proposed || {}).length}
                        <div class="change-list">
                            {#each Object.entries(entry.proposed) as change}<div><b>{change[0]}</b><span>{String(entry.listing[change[0]] || "Not set")} → {String(change[1])}</span></div>{/each}
                        </div>
                        <div class="actions">
                            <button on:click={() => selected = entry}>Explain Recommendation</button>
                            <button class="primary" disabled={loading || entry.status === "Applied"} on:click={() => approve(entry)}>{entry.status === "Applied" ? "Applied" : "Approve & Apply"}</button>
                        </div>
                    {:else}<p class="help">No field changes recommended from the available evidence.</p>{/if}
                </article>
            {/each}
        </div>
        {#if selected}
            <aside class="panel nested-panel explanation">
                <h3>Why this recommendation?</h3>
                <p>{selected.reason}</p>
                <p><b>Confidence:</b> {selected.confidence}. <b>Risk:</b> {selected.risk}.</p>
                <ul>{#each selected.findings as finding}<li><b>{finding.field}:</b> {finding.message}</li>{/each}</ul>
                <button on:click={() => selected = null}>Close</button>
            </aside>
        {/if}
    </div>
    <div class="panel nested-panel history-panel">
        <h3>Change history</h3>
        {#if !history.length}<p class="empty">No approved changes yet.</p>{/if}
        {#each history.slice(0, 10) as entry}<div class="history-row"><strong>{entry.listing.title || entry.listing.sku}</strong><span>{entry.status} · {new Date(entry.createdAt).toLocaleString()}</span></div>{/each}
    </div>
</section>
