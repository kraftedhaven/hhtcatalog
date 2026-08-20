export default function SkuCard({ sku, title, category, conditionId }) {
  return (
    <section className="panel panel-compact">
      <div className="section-heading slim">
        <div>
          <p className="eyebrow">SKU</p>
          <h2>Catalog identifier</h2>
        </div>
      </div>

      <div className="sku-display">{sku || 'HHT-UNASSIGNED'}</div>
      <dl className="detail-list">
        <div>
          <dt>Title</dt>
          <dd>{title || 'Waiting for analysis'}</dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{category || 'Not mapped'}</dd>
        </div>
        <div>
          <dt>Condition</dt>
          <dd>{conditionId || '3000'}</dd>
        </div>
      </dl>
    </section>
  );
}