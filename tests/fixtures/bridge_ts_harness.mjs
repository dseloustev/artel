#!/usr/bin/env node
/**
 * The actual-TypeScript harness for the bridge's session-baseline capture.
 *
 * Loads the real `opencode/plugin/artel.ts` (Node's type stripping), registers it on a fake
 * native client, and drives the session lifecycle the test asks for. Only the native client
 * is fake: the hook spawns, the scratch host and `hooks/session_baseline.py` are real.
 *
 * Usage:
 *   node bridge_ts_harness.mjs <bridge.ts> <install-root> <host-dir> <mode> <session-id>
 *
 * `mode` is `created` (the subscription delivers `session.created` before the first context
 * call) or `missed` (it delivers nothing — a freshly started server whose subscription came
 * up after the event). The harness prints one JSON line: {baseline, created, contextInjected}.
 */
import fs from "node:fs"
import module from "node:module"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

// The bridge imports its sibling extensionless (`./parallel`) the way the plugin host
// resolves it. Node's ESM resolver does not, so the harness supplies exactly that rule:
// an extensionless relative specifier resolves to the sibling `.ts` file when one exists.
module.registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !path.extname(specifier) && context.parentURL) {
      const candidate = path.resolve(
        path.dirname(fileURLToPath(context.parentURL)),
        specifier + ".ts",
      )
      if (fs.existsSync(candidate)) {
        return { url: pathToFileURL(candidate).href, shortCircuit: true }
      }
    }
    return nextResolve(specifier, context)
  },
})

const [bridgePath, installRoot, hostDir, mode, sessionID] = process.argv.slice(2)

const hooks = { tool: new Map(), session: new Map() }
const tools = new Map()

const ctx = {
  app: { version: "2.0.32" },
  location: { directory: hostDir },
  model: { list: async () => [] },
  tool: {
    transform: (callback) =>
      callback({
        namespace: () => {},
        add: (spec) => tools.set(spec.name, spec),
      }),
    hook: async (name, handler) => {
      hooks.tool.set(name, handler)
    },
  },
  session: {
    hook: async (name, handler) => {
      hooks.session.set(name, handler)
    },
    get: async ({ sessionID: id }) => ({
      data: { id, location: { directory: hostDir } },
    }),
    create: async () => {
      throw new Error("the harness never creates a session")
    },
    prompt: async () => {
      throw new Error("the harness never prompts a session")
    },
    interrupt: async () => {
      throw new Error("the harness never interrupts a session")
    },
    context: async () => [],
  },
  event: {
    subscribe: async function* ({ signal }) {
      if (mode === "created") {
        yield {
          type: "session.created",
          data: { sessionID, location: { directory: hostDir } },
        }
      }
      await new Promise((resolve) => {
        if (signal.aborted) return resolve()
        signal.addEventListener("abort", resolve, { once: true })
      })
    },
  },
}

const { createArtelPlugin } = await import(pathToFileURL(bridgePath).href)
const plugin = createArtelPlugin({ root: installRoot })
const cleanup = await plugin.setup(ctx)

const baselinePath = path.join(hostDir, ".artel", "run", ".hooks", `baseline-${sessionID}.json`)

async function waitForBaseline(deadlineMs = 30000) {
  const deadline = Date.now() + deadlineMs
  while (Date.now() < deadline) {
    if (fs.existsSync(baselinePath)) return true
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  return false
}

// A `created` run has already delivered the event through the subscription; give the
// fire-and-forget baseline hook the same bounded wait before the context call.
const createdBaseline = mode === "created" ? await waitForBaseline() : null

const firstUser = { role: "user", content: [{ type: "text", text: "hello" }] }
const contextHook = hooks.session.get("context")
if (!contextHook) {
  console.error("the harness found no session context hook")
  process.exit(2)
}
await contextHook({ sessionID, messages: [firstUser] })
const contextInjected = Array.isArray(firstUser.content)
  && firstUser.content.some((part) => part && part.type === "text"
    && String(part.text || "").includes("This repository is configured for artel"))

const baseline = await waitForBaseline()
if (typeof cleanup === "function") cleanup()

console.log(JSON.stringify({
  baseline,
  created_baseline: createdBaseline,
  context_injected: contextInjected,
  baseline_keys: baseline
    ? JSON.parse(fs.readFileSync(baselinePath, "utf-8")).keys ?? null
    : null,
}))
