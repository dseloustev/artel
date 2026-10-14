# Review notes

Working notes for the session-kit review. Keep prose only; operational steps live in `tools/`.

- The memory store keeps sessions until process exit; persistence is out of scope for this PR.
- The contract change makes a missing session an error instead of a `null` return.
- Follow-up: a file-backed store is starting in `src/domain/file_session_store.ts`.
