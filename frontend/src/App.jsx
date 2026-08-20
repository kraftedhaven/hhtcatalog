import { useMemo, useState } from 'react';
import FileUpload from './components/FileUpload';
import VisionResults from './components/VisionResults';
import SkuCard from './components/SkuCard';
import PricingCard from './components/PricingCard';
import SeoListing from './components/SeoListing';
import { analyzeImages, getApiBaseUrl } from './lib/analyze';

export default function App() {
  const [files, setFiles] = useState([]);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const stats = useMemo(() => {
    return [
      { label: 'Images queued', value: String(files.length) },
      { label: 'API endpoint', value: getApiBaseUrl() },
      { label: 'Ready sections', value: result ? '5' : '0' }
    ];
  }, [files.length, result]);

  async function handleAnalyze() {
    if (!files.length || status === 'loading') return;
    setStatus('loading');
    setError('');

    try {
      const analyzed = await analyzeImages(files);
      setResult(analyzed);
      setStatus('success');
    } catch (analysisError) {
      setError(analysisError.message || 'Analysis failed.');
      setStatus('error');
    }
  }

  return (
    <div className="app-shell">
      <div className="background-orb orb-one" />
      <div className="background-orb orb-two" />
      <div className="background-grid" />

      <main className="layout">
        <header className="hero panel">
          <div className="hero-copy">
            <p className="eyebrow">Hidden Haven Threads</p>
            <h1>React dashboard for product image analysis.</h1>
            <p className="hero-text">
              Upload photos, send them to your <code>/analyze</code> endpoint, and review SKU, pricing, SEO, and vision output in a single workspace.
            </p>
          </div>

          <div className="stat-row">
            {stats.map((stat) => (
              <article className="stat-card" key={stat.label}>
                <span>{stat.label}</span>
                <strong>{stat.value}</strong>
              </article>
            ))}
          </div>
        </header>

        {error ? <div className="error-banner">{error}</div> : null}

        <section className="dashboard-grid">
          <div className="dashboard-column">
            <FileUpload
              files={files}
              onFilesChange={setFiles}
              onClear={() => {
                setFiles([]);
                setResult(null);
                setError('');
                setStatus('idle');
              }}
              onAnalyze={handleAnalyze}
              status={status}
            />

            <div className="mini-grid two-up">
              <SkuCard
                sku={result?.sku}
                title={result?.title}
                category={result?.category}
                conditionId={result?.conditionId}
              />
              <PricingCard pricing={result?.pricing} title={result?.title} />
            </div>
          </div>

          <div className="dashboard-column">
            <SeoListing seo={result?.seo} />
            <VisionResults vision={result?.vision} raw={result?.raw} />
          </div>
        </section>
      </main>
    </div>
  );
}