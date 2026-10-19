#!/usr/bin/env node
/**
 * The actual-TypeScript harness for tests/test_parallel_adapter.py.
 *
 * Loads the real `opencode/plugin/parallel.ts` (Node's type stripping), registers it on a
 * fake native client, and drives exactly one registered tool. The fake client is the only
 * injected boundary: the policy half, the launch journal and the repository-common guard are
 * the real ones.
 *
 * Usage:
 *   node parallel_ts_harness.mjs <adapter.ts> <helper-root> <repo> <caller-id> \
 *        <tool> <input-json> [barrier-file]
 *
 * The fake `session.interrupt` counts its calls and, with a barrier file, blocks until the
 * test creates it. The harness prints one JSON line: {calls, error, result}.
 */
import fs from "node:fs"
import { pathToFileURL } from "node:url"

const [adapterPath, helperRoot, repo, callerId, toolName, inputJson, barrierFile] =
  process.argv.slice(2)

const input = JSON.parse(inputJson)
const calls = { get: 0, create: 0, prompt: 0, context: 0, interrupt: 0 }
const tools = new Map()

const ctx = {
  app: { version: "2.0.32" },
  location: { directory: repo },
  model: { list: async () => [] },
  tool: {
    transform: (callback) =>
      callback({
        namespace: () => {},
        add: (spec) => tools.set(spec.name, spec),
      }),
  },
  session: {
    get: async ({ sessionID }) => {
      calls.get += 1
      if (sessionID !== callerId) return null
      return { id: callerId, location: { directory: repo } }
    },
    context: async () => {
      calls.context += 1
      return []
    },
    create: async () => {
      calls.create += 1
      throw new Error("the harness never creates a session")
    },
    prompt: async () => {
      calls.prompt += 1
      throw new Error("the harness never prompts a session")
    },
    interrupt: async () => {
      calls.interrupt += 1
      if (barrierFile) {
        const deadline = Date.now() + 30000
        while (!fs.existsSync(barrierFile)) {
          if (Date.now() > deadline) throw new Error("the interrupt barrier deadline expired")
          await new Promise((resolve) => setTimeout(resolve, 20))
        }
      }
      return { interrupted: true }
    },
  },
}

const { registerParallel } = await import(pathToFileURL(adapterPath).href)
registerParallel(ctx, { root: helperRoot })
const spec = tools.get(toolName)
if (!spec) {
  console.error(`the harness found no tool ${toolName}`)
  process.exit(2)
}

let result = null
let error = null
try {
  result = await spec.execute(input, { sessionID: callerId })
} catch (caught) {
  error = String(caught && caught.message ? caught.message : caught)
}
console.log(JSON.stringify({ calls, error, result }))
