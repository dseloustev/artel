# Path conventions for artifact content

*Status: draft v0.1 · 2026-08-01*

This document defines how file paths must appear **inside** artifacts that agents write to
`<specs.dir>` (PRDs, plans, research, tasklists, reviews, QA reports, visions, ADRs, summaries,
etc.).

All artifact-writing agents reference this document. Do not duplicate the rule in agent prompts
— link to this file instead.

---

## 1. The Rule

**All paths written into artifact content MUST be repo-relative.** Never write an absolute
filesystem path into a `<specs.dir>` artifact, regardless of section or context.

Forbidden prefixes in artifact content:

- `/Users/...` (macOS)
- `/home/...` (Linux)
- `C:\...`, `D:\...`, `\\?\...` (Windows)
- Any other absolute filesystem prefix that identifies the local machine's layout

Allowed forms:

- Repo-relative paths: `src/auth/session.ts`, `tests/auth/session.test.ts`,
  `<specs.dir>/PROJ-123/phase-1/prd.md`
- Citation forms: `src/auth/session.ts:47`, `src/auth/session.ts:47-48`,
  `src/auth/session.ts:71–79`
- Code-fenced or backticked paths in the same repo-relative form

---

## 2. Rationale

Spec-trail documents are shared: committed to the repository on the files path, stored in
kartoteka and read from other machines on the kartoteka path ([spec-storage.md](spec-storage.md)). Absolute paths:

1. **Leak local machine layout** to every collaborator who pulls the repo (e.g., usernames,
   project directory structure).
2. **Are useless on any machine but the author's** — they cannot be opened by another developer
   or by CI.
3. **Resist tooling** — search, link generation, and IDE jump-to-file all expect repo-relative
   paths.

---

## 3. Scope

The rule applies to **every** section of every artifact, including:

- Inline citations in prose ("see `src/auth/session.ts:47`")
- Tables of references
- Footers/appendices such as "Files referenced", "Reviewed artifacts", "Inputs", "Reviewed
  subjects"
- Comments embedded in YAML/JSON/Markdown front-matter

When a footer or appendix only re-lists files already cited inline, prefer to **drop the
footer** rather than maintain a parallel list.

---

## 4. What is NOT covered

This rule governs the **content written into artifact files**. It does NOT restrict:

- Arguments passed to tools (`Read`, `Write`, `Edit`, `Bash`, etc.) — those may use absolute
  paths.
- Paths the user types in chat or in their own shell.
- External URLs (RFC links, issue-tracker links, PR links, etc.) — those are not filesystem
  paths.

If an agent needs to record the repo root for an external reader, it should write the
repository name (e.g., `example-repo`) rather than the local checkout path.

---

## 5. Enforcement check

Before finishing any write to a `<specs.dir>` artifact, the agent should mentally run:

```
grep -E '(^|[^A-Za-z])(/Users/|/home/|[A-Z]:\\\\)' <artifact>
```

If any match is found, rewrite those lines as repo-relative paths before saving.
