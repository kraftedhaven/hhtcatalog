const ORIGIN = "https://hht-catalog-b34ed1b32417.herokuapp.com";
const OAUTH_PATHS = ["/api/ebay/oauth/start", "/api/ebay/oauth/callback", "/api/ebay/oauth/status"];

export default {
  async fetch(request) {
    const url = new URL(request.url);
    // Keep OAuth endpoints available, but never cache tokens, callbacks, or POSTs.
    if (OAUTH_PATHS.some((path) => url.pathname === path || url.pathname.startsWith(path + "/"))) {
      return fetch(new Request(ORIGIN + url.pathname + url.search, request));
    }
    const target = new URL(ORIGIN + url.pathname + url.search);
    const init = { method: request.method, headers: request.headers, body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body, redirect: "follow" };
    const cacheable = request.method === "GET" && ["/", "/health"].includes(url.pathname);
    if (cacheable) {
      const cached = await caches.default.match(request);
      if (cached) return cached;
    }
    const response = await fetch(new Request(target, init));
    if (cacheable && response.ok) {
      const copy = new Response(response.body, response);
      copy.headers.set("Cache-Control", "public, max-age=30");
      await caches.default.put(request, copy.clone());
      return copy;
    }
    return response;
  },
};
