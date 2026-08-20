export default function PricingCard({ pricing, title }) {
  return (
    <section className="panel panel-compact pricing-panel">
      <div className="section-heading slim">
        <div>
          <p className="eyebrow">Pricing</p>
          <h2>Suggested listing value</h2>
        </div>
      </div>

      <div className="pricing-value">{pricing?.formatted || '$0.00'}</div>
      <p className="support-text">{pricing?.rationale || `The endpoint can infer a starting price for ${title || 'this item'}.`}</p>

      <div className="mini-grid">
        <div>
          <span>Suggested</span>
          <strong>{pricing?.formatted || '$0.00'}</strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>{pricing?.confidence || 'AI estimate'}</strong>
        </div>
      </div>
    </section>
  );
}