const DEFAULT_API_BASE_URL = 'http://localhost:8080';

export function getApiBaseUrl() {
  const value = import.meta.env.VITE_API_BASE_URL?.trim();
  return value || DEFAULT_API_BASE_URL;
}

export async function analyzeImages(files) {
  const apiBaseUrl = getApiBaseUrl().replace(/\/$/, '');
  const formData = new FormData();

  files.forEach((file) => {
    formData.append('files', file);
  });

  const response = await fetch(`${apiBaseUrl}/analyze`, {
    method: 'POST',
    body: formData
  });

  const rawText = await response.text();
  let data = null;

  try {
    data = rawText ? JSON.parse(rawText) : null;
  } catch {
    data = { raw: rawText };
  }

  if (!response.ok) {
    const message = data?.error || data?.detail || 'Upload failed.';
    throw new Error(message);
  }

  return normalizeAnalysis(data, files);
}

function normalizeAnalysis(payload, files) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const title = String(source.title || source.name || '').trim();
  const category = String(source.category || source.department || 'General').trim();
  const description = String(source.description || source.summary || '').trim();
  const priceValue = coerceNumber(source.price ?? source.suggested_price ?? source.listing_price);
  const conditionId = String(source.condition_id || source.conditionId || '3000');

  const sku = String(
    source.sku || buildSku(title, category, conditionId, files)
  ).toUpperCase();

  const seoTitle = String(source.seo_title || buildSeoTitle(title, category, conditionId)).trim();
  const seoDescription = String(source.seo_description || buildSeoDescription(description, title, category)).trim();
  const seoSlug = String(source.seo_slug || buildSlug(title || category || 'listing')).trim();
  const seoKeywords = normalizeKeywords(source.seo_keywords || buildKeywords(title, category, description));

  const vision = source.vision || {
    summary: description || 'No vision summary returned by the API yet.',
    observations: buildVisionObservations(source, files)
  };

  const pricing = source.pricing || {
    suggested: priceValue,
    formatted: formatCurrency(priceValue),
    rationale: source.pricing_rationale || 'Derived from the API response.'
  };

  return {
    raw: source,
    title,
    category,
    description,
    conditionId,
    price: priceValue,
    sku,
    pricing,
    seo: {
      title: seoTitle,
      description: seoDescription,
      slug: seoSlug,
      keywords: seoKeywords
    },
    vision,
    imageCount: files.length
  };
}

function coerceNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number.parseFloat(String(value).replace(/[^0-9.-]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function buildSku(title, category, conditionId, files) {
  const titleSeed = slugChunk(title || category || 'item', 4);
  const categorySeed = slugChunk(category || 'general', 2);
  const imageSeed = String(files.length || 1).padStart(2, '0');
  return `HHT-${titleSeed}-${categorySeed}-${conditionId}-${imageSeed}`;
}

function slugChunk(value, wordCount) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, wordCount)
    .map((word) => word.slice(0, 3).toUpperCase())
    .join('');
}

function buildSeoTitle(title, category, conditionId) {
  const conditionLabel = conditionId === '1000' ? 'New' : 'Pre-Owned';
  const base = title || category || 'Vintage Listing';
  return `${base} | ${conditionLabel} | Hidden Haven Threads`;
}

function buildSeoDescription(description, title, category) {
  const source = description || `${title || 'Curated piece'} in ${category || 'general apparel'}.`;
  return `${source} Optimized for search, marketplace visibility, and fast cataloging.`;
}

function buildSlug(value) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function buildKeywords(title, category, description) {
  const keywords = [title, category, description]
    .filter(Boolean)
    .join(' ')
    .split(/[,/\s]+/)
    .map((word) => word.trim().toLowerCase())
    .filter((word) => word.length > 3);

  return [...new Set(keywords)].slice(0, 12).join(', ');
}

function normalizeKeywords(value) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(', ');
  }

  return String(value || '')
    .split(',')
    .map((word) => word.trim())
    .filter(Boolean)
    .join(', ');
}

function formatCurrency(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD'
  }).format(Number.isFinite(value) ? value : 0);
}

function buildVisionObservations(source, files) {
  const observations = [];

  if (source.title) observations.push(`Title inferred: ${source.title}`);
  if (source.category) observations.push(`Category mapped to ${source.category}`);
  if (source.condition_id) observations.push(`Condition ID: ${source.condition_id}`);
  observations.push(`${files.length} image${files.length === 1 ? '' : 's'} uploaded`);

  return observations;
}