/* Service worker: after the first visit the whole app (including the 17 MB
 * Python runtime) is served from the browser's cache, so it works offline
 * and later visits start instantly. `precache.json` is written by
 * tools/build_web.py and carries a version; a new build gets a new cache and
 * the old one is deleted on activation. Only same-origin GETs are cached. */
"use strict";

const PRECACHE_URL = "precache.json";

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const manifest = await fetch(PRECACHE_URL, { cache: "no-cache" }).then((r) => r.json());
    const cache = await caches.open("dpt-" + manifest.version);
    await cache.addAll(manifest.files);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const manifest = await fetch(PRECACHE_URL, { cache: "no-cache" }).then((r) => r.json()).catch(() => null);
    const keep = manifest ? "dpt-" + manifest.version : null;
    for (const name of await caches.keys()) if (name.startsWith("dpt-") && name !== keep) await caches.delete(name);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  const url = new URL(req.url);
  // Only the pinned runtime is immutable. core.zip holds the Python core and
  // is rebuilt with every change, so serving it from cache would leave a
  // returning visitor running yesterday's Python behind today's JavaScript.
  const immutable = url.pathname.includes("/vendor/");
  event.respondWith((async () => {
    if (!immutable) {
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok && fresh.type === "basic") {
          const names = (await caches.keys()).filter((n) => n.startsWith("dpt-"));
          if (names.length) (await caches.open(names[names.length - 1])).put(req, fresh.clone());
          return fresh;
        }
      } catch (_) { /* offline: fall through to the cache */ }
    }
    const cached = await caches.match(req, { ignoreSearch: true });
    if (cached) return cached;
    const response = await fetch(req);
    if (response.ok && response.type === "basic") {
      const names = (await caches.keys()).filter((n) => n.startsWith("dpt-"));
      if (names.length) (await caches.open(names[names.length - 1])).put(req, response.clone());
    }
    return response;
  })());
});
