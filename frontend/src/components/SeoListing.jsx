export default function SeoListing({ seo }) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SEO listing</p>
          <h2>Search-ready metadata</h2>
        </div>
      </div>

      <div className="seo-card">
        <p className="seo-title">{seo?.title || 'No SEO title yet'}</p>
        <p className="seo-url">/{seo?.slug || 'listing-slug'}</p>
        <p className="seo-description">{seo?.description || 'A strong meta description will appear here after analysis.'}</p>
      </div>

      <div className="keyword-cloud">
        {(seo?.keywords || '')
          .split(',')
          .map((keyword) => keyword.trim())
          .filter(Boolean)
          .map((keyword) => (
            <span key={keyword} className="keyword-pill">
              {keyword}
            </span>
          ))}
      </div>
    </section>
  );
}