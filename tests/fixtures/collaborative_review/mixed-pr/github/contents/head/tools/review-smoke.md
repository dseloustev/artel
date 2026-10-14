# Session store smoke test

Operator steps after touching `src/domain/session*`:

1. `npm ci && npm run build`
2. `node tools/smoke.js --store memory`
3. Expect `smoke: ok`; a `SessionNotFound` for a live session id means the store contract regressed.
