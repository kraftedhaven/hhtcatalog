export default function VisionResults({ vision, raw }) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Vision results</p>
          <h2>Model output</h2>
        </div>
      </div>

      <div className="result-stack">
        <div className="callout">
          <h3>{vision?.summary || 'Awaiting analysis.'}</h3>
          <p>This section surfaces the raw interpretation returned by your endpoint and keeps the response visible for auditability.</p>
        </div>

        <div className="chip-row">
          {(vision?.observations || []).map((item) => (
            <span className="info-chip" key={item}>{item}</span>
          ))}
        </div>

        <div className="json-box">
          <div className="json-box-head">
            <span>Response snapshot</span>
          </div>
          <pre>{JSON.stringify(raw || {}, null, 2)}</pre>
        </div>
      </div>
    </section>
  );
}