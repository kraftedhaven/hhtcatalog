import { useEffect, useMemo, useRef, useState } from 'react';
import { getApiBaseUrl } from '../lib/analyze';

export default function FileUpload({ files, onFilesChange, onClear, onAnalyze, status }) {
  const inputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);

  const previews = useMemo(
    () =>
      files.map((file) => ({
        file,
        url: URL.createObjectURL(file)
      })),
    [files]
  );

  useEffect(() => {
    return () => {
      previews.forEach((preview) => URL.revokeObjectURL(preview.url));
    };
  }, [previews]);

  function addFiles(nextFiles) {
    const accepted = Array.from(nextFiles).filter((file) => file.type.startsWith('image/'));
    if (accepted.length) {
      onFilesChange([...files, ...accepted]);
    }
    if (inputRef.current) {
      inputRef.current.value = '';
    }
  }

  return (
    <section className="panel upload-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Image intake</p>
          <h2>Upload product photos</h2>
        </div>
        <span className="status-chip">API: {getApiBaseUrl()}</span>
      </div>

      <div
        className={`dropzone ${dragActive ? 'dropzone-active' : ''}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragActive(false);
          addFiles(event.dataTransfer.files);
        }}
      >
        <input
          ref={inputRef}
          className="visually-hidden"
          type="file"
          accept="image/*"
          multiple
          onChange={(event) => addFiles(event.target.files || [])}
        />

        <div className="dropzone-copy">
          <span className="dropzone-icon">↗</span>
          <h3>Drop files here, or click to browse.</h3>
          <p>Send front, back, tag, and detail shots in a single request. The endpoint expects multipart form data with the key <strong>files</strong>.</p>
        </div>

        <div className="dropzone-actions">
          <button type="button" className="button button-secondary" onClick={() => inputRef.current?.click()}>
            Choose images
          </button>
          <button type="button" className="button button-primary" onClick={onAnalyze} disabled={!files.length || status === 'loading'}>
            {status === 'loading' ? 'Analyzing...' : 'Run analysis'}
          </button>
        </div>
      </div>

      <div className="preview-header">
        <p>{files.length} file{files.length === 1 ? '' : 's'} selected</p>
        <button type="button" className="text-button" onClick={onClear} disabled={!files.length}>
          Clear all
        </button>
      </div>

      <div className="preview-grid">
        {previews.length ? (
          previews.map((preview, index) => (
            <figure className="preview-card" key={`${preview.file.name}-${index}`}>
              <img src={preview.url} alt={preview.file.name} />
              <figcaption>
                <span>{preview.file.name}</span>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => onFilesChange(files.filter((_, fileIndex) => fileIndex !== index))}
                  aria-label={`Remove ${preview.file.name}`}
                >
                  ×
                </button>
              </figcaption>
            </figure>
          ))
        ) : (
          <div className="preview-empty">
            <span>No images queued.</span>
            <p>Once you add images, this panel becomes the first validation step before sending the API request.</p>
          </div>
        )}
      </div>
    </section>
  );
}