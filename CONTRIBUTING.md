# Contributing

## Developing the web app

The web app lives in `web/` (Vite, React, TypeScript; Node 22.12 or newer). Run the service as in [Running with Python](docs/installation.md#running-with-python-no-docker), then in another terminal:

```
cd web
npm install
npm run dev        # http://localhost:5173, proxying /api to the service on :8080
```

`AUDIBLE_SYNC_API=http://host:port npm run dev` points the proxy elsewhere. The checks CI runs are `npm run lint`, `npm run typecheck`, `npm test` and `npm run build`; `npm run format` fixes the formatting. After changing `src/schemas.py` or a route, run `npm run gen:api` to regenerate `web/openapi.json` and the client's types in `web/src/api/schema.d.ts` (it needs `uv`); CI fails while they are stale. `npm run build` writes `web/dist`, which the service serves when it finds it.

## Testing by hand

[`docs/manual-testing.md`](docs/manual-testing.md) has the checks for what unit tests cannot reach: the download pipeline, a real Audible account and the web app in a browser.
