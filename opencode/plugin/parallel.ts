/**
 * artel parallel worker-session adapter for OpenCode 2.x.
 *
 * The thin transport half of the Task 7 adapter (docs/opencode-parallel.md §2): four tools
 * registered by the bridge (`opencode/plugin/artel.ts`), each one a small sequence of
 * installed 2.0.32 plugin-client calls bracketed by the policy half
 * (`scripts/parallel_launch.py`), which owns every decision -- request validation, the
 * capability gate, the launch journal, session verification, result recovery and
 * interruption semantics. Nothing here re-implements a schema or a lifecycle rule.
 *
 *   workerCapabilities  read-only report: the installed version and the client surface the
 *                       transport needs; a worker caller is refused (policy half).
 *   startWorker         persist the launch intent, create the pinned session, verify it,
 *                       persist its id, admit the pinned prompt; duplicate starts and
 *                       crash windows reconcile from durable state instead of creating a
 *                       second session.
 *   workerStatus        durable session state plus the validated `<artifact_root>/result.json`;
 *                       idle and native success are not completion, and an interruption wins
 *                       over a contradictory success result.
 *   interruptWorker     interrupt the one known session and report the durable outcome.
 *
 * The adapter never schedules, claims, releases, publishes, commits, edits tasklists, answers
 * permissions or touches the event stream -- events are live-only hints and never recovery.
 * Worker-mode restrictions: the bridge denies this namespace to worker locations, and the
 * policy half independently refuses a caller that sits in a pinned worker root.
 *
 * The default export is intentionally absent: the bridge imports `registerParallel`, and a
 * standalone loader (a native proof wrapper) appends its own plugin definition.
 */
import { spawn } from "node:child_process"
import os from "node:os"
import path from "node:path"

const CONFIG_HOME = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config")
const ARTEL_ROOT = process.env.ARTEL_ROOT || path.join(CONFIG_HOME, "opencode", "artel")

/** The tool namespace and the exposed tool ids (dots become `_` in the exposed id). */
export const TOOL_NAMESPACE = "artel.parallel"
export const PARALLEL_TOOL_IDS = [
  "artel_parallel_workerCapabilities",
  "artel_parallel_coordinatorIdentity",
  "artel_parallel_startWorker",
  "artel_parallel_workerStatus",
  "artel_parallel_interruptWorker",
]

const HELPER = "parallel_launch.py"
const HELPER_TIMEOUT_MS = 60_000
const OUTPUT_TAIL = 2_000
const GUARD_READY_TIMEOUT_MS = 30_000
const GUARD_RELEASE_TIMEOUT_MS = 60_000

type ParallelOptions = { root?: string }

type CoordinatorGuard = { release: () => Promise<void> }

function helperPath(options: ParallelOptions): string {
  return path.join(options.root || ARTEL_ROOT, "scripts", HELPER)
}

/** One helper invocation: JSON on stdin, one JSON envelope on stdout. */
function runHelper(helper: string, command: string, payload: unknown, cwd: string): Promise<any> {
  return new Promise((resolve, reject) => {
    let stdout = ""
    let stderr = ""
    const child = spawn("python3", [helper, command], { cwd })
    const timer = setTimeout(() => child.kill("SIGKILL"), HELPER_TIMEOUT_MS)
    child.stdout.on("data", (chunk) => (stdout += chunk))
    child.stderr.on("data", (chunk) => (stderr += chunk))
    child.stdin.on("error", () => {})
    child.on("error", (error) => {
      clearTimeout(timer)
      reject(new Error(`artel parallel adapter: cannot run the policy helper ${helper}: ${error}`))
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      let envelope: any = null
      for (const line of stdout.split("\n")) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          envelope = JSON.parse(trimmed)
          break
        } catch {
          // keep scanning
        }
      }
      if (!envelope) {
        reject(new Error(
          `artel parallel adapter: the policy helper produced no envelope (exit ${code}): ` +
          (stderr.trim() || stdout.trim()).slice(-OUTPUT_TAIL)))
        return
      }
      if (envelope.ok !== true) {
        const kind = envelope.error?.kind ?? "adapter_error"
        const message = envelope.error?.message ?? "the policy helper refused"
        reject(new Error(`artel parallel adapter: ${kind}: ${message}`))
        return
      }
      resolve(envelope.data)
    })
    child.stdin.write(JSON.stringify(payload))
    child.stdin.end()
  })
}

/** One ephemeral Python child holding the repository-common coordinator guard.

 * The native adapter's awaited RPC sequence (session create/prompt/interrupt plus the
 * journal writes around them) spans several subprocess calls; a read-only policy check
 * would not exclude a concurrent `owner recover` while the RPC is in flight. The child
 * takes the same guard the CLI takes, validates the owner and the calling coordinator
 * inside it, and keeps the kernel lock until its stdin closes -- released on EOF, on error
 * or by the kernel when the child dies. It is lifetime plumbing only: every decision stays
 * in the policy half. */
async function acquireGuard(
  helper: string,
  cwd: string,
  identity: any,
  coordinatorId: string,
): Promise<CoordinatorGuard> {
  const child = spawn("python3", [helper, "guard-hold"], { cwd })
  let stdout = ""
  let stderr = ""
  const ready = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL")
      reject(new Error(
        `artel parallel adapter: guard_timeout: the coordinator guard did not become ready`))
    }, GUARD_READY_TIMEOUT_MS)
    const settle = (error: Error | null) => {
      clearTimeout(timer)
      if (error) reject(error)
      else resolve()
    }
    child.stdout.on("data", (chunk) => {
      stdout += chunk
      const line = stdout.split("\n").map((entry) => entry.trim()).find((entry) => entry)
      if (!line) return
      let envelope: any = null
      try {
        envelope = JSON.parse(line)
      } catch {
        return
      }
      if (envelope.ok === true) {
        settle(null)
        return
      }
      settle(new Error(
        `artel parallel adapter: ${envelope.error?.kind ?? "guard_error"}: ` +
        `${envelope.error?.message ?? "the coordinator guard refused"}`))
    })
    child.stderr.on("data", (chunk) => (stderr += chunk))
    child.on("error", (error) => settle(new Error(
      `artel parallel adapter: cannot run the policy helper ${helper}: ${error}`)))
    child.on("close", (code) => settle(new Error(
      `artel parallel adapter: guard_error: the coordinator guard exited before ready ` +
      `(exit ${code}): ${(stderr.trim() || stdout.trim()).slice(-OUTPUT_TAIL)}`)))
  })
  child.stdin.on("error", () => {})
  child.stdin.write(JSON.stringify({
    repo: cwd,
    ticket: identity?.ticket,
    run_id: identity?.run_id,
    phase: identity?.phase ?? null,
    coordinator_id: coordinatorId,
  }) + "\n")
  await ready
  let released = false
  return {
    release: async () => {
      if (released) return
      released = true
      child.stdin.end()
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          child.kill("SIGKILL")
          resolve()
        }, GUARD_RELEASE_TIMEOUT_MS)
        child.on("close", () => {
          clearTimeout(timer)
          resolve()
        })
      })
    },
  }
}

/** The installed plugin host facts the policy half checks before any effect. */
function capabilityFacts(ctx: any): { version: string | null; api: Record<string, boolean> } {
  const app = ctx?.app ?? {}
  const session = ctx?.session ?? {}
  const model = ctx?.model ?? {}
  return {
    version: typeof app.version === "string" ? app.version : null,
    api: {
      "app.version": typeof app.version === "string" && app.version.length > 0,
      "model.list": typeof model.list === "function",
      "session.create": typeof session.create === "function",
      "session.get": typeof session.get === "function",
      "session.prompt": typeof session.prompt === "function",
      "session.interrupt": typeof session.interrupt === "function",
      "session.context": typeof session.context === "function",
    },
  }
}

function unwrap(value: any): any {
  return value && typeof value === "object" && "data" in value ? value.data : value
}

function isNotFound(error: any): boolean {
  // The installed plugin client throws `{_tag: "Session.NotFoundError", sessionID}` with an
  // EMPTY `message` (probed natively on 2.0.32; originally 2.0.29), so the tag/name/string
  // forms are checked too -- matching only `message` would turn a missing session into a
  // thrown tool error.
  const text = [error?.message, error?._tag, error?.tag, error?.name, String(error)]
    .filter((value) => typeof value === "string" && value.length > 0)
    .join(" ")
  return /not.?found|404/i.test(text)
}

async function getSession(ctx: any, sessionID: string): Promise<any | null> {
  try {
    return unwrap(await ctx.session.get({ sessionID }))
  } catch (error) {
    if (isNotFound(error)) return null
    throw error
  }
}

async function readMessages(ctx: any, sessionID: string): Promise<any[]> {
  try {
    const result = unwrap(await ctx.session.context({ sessionID }))
    return Array.isArray(result) ? result : []
  } catch (error) {
    if (isNotFound(error)) return []
    throw error
  }
}

function parseModelRef(value: unknown): { providerID: string; id: string; variant?: string } | null {
  if (typeof value !== "string") return null
  const [modelPart, variant] = value.split("#", 2)
  const slash = modelPart.indexOf("/")
  if (slash <= 0 || slash === modelPart.length - 1) return null
  const ref: { providerID: string; id: string; variant?: string } = {
    providerID: modelPart.slice(0, slash),
    id: modelPart.slice(slash + 1),
  }
  if (variant) ref.variant = variant
  return ref
}

/** The installed model list, as the two facts the policy half consumes. */
async function modelFacts(ctx: any, model: unknown): Promise<{ available: boolean | null; variants: string[] }> {
  const ref = parseModelRef(model)
  if (!ref) return { available: false, variants: [] }
  try {
    const result = unwrap(await ctx.model.list())
    const list: any[] = Array.isArray(result) ? result : []
    const found = list.find(
      (entry) => entry?.providerID === ref.providerID && (entry?.modelID === ref.id || entry?.id === ref.id),
    )
    if (!found) return { available: false, variants: [] }
    const variants = Array.isArray(found.variants)
      ? found.variants.map((variant: any) => variant?.id ?? variant).filter((id: any) => typeof id === "string")
      : []
    return { available: true, variants }
  } catch {
    return { available: null, variants: [] }
  }
}

/** The calling session's own location -- the authoritative worker-root check is policy. */
async function resolveCaller(ctx: any, toolContext: any): Promise<{ session_id: string; location: string }> {
  const sessionID = toolContext?.sessionID
  if (typeof sessionID !== "string" || !sessionID) {
    throw new Error("artel parallel adapter: caller_unknown: the tool call carries no session id")
  }
  const session = await getSession(ctx, sessionID)
  const directory = session?.location?.directory
  if (typeof directory !== "string" || !directory) {
    throw new Error(
      `artel parallel adapter: caller_unknown: cannot resolve the location of session ${sessionID}`)
  }
  return { session_id: sessionID, location: directory }
}

/** The tool input, normalized to the strict policy request (missing permissions are null). */
function normalizeRequest(input: any): unknown {
  const prompt = input?.prompt ?? {}
  const request: any = {
    assignment: input?.assignment,
    agent: input?.agent,
    prompt: { text: prompt.text },
    permissions: input?.permissions ?? null,
  }
  if (prompt.delivery) request.prompt.delivery = prompt.delivery
  return request
}

/** The status/interrupt input: the strict assignment-only request. */
function normalizeAssignmentInput(input: any): unknown {
  return { assignment: input?.assignment }
}

function toolResult(output: any) {
  return { output, content: [{ type: "text", text: JSON.stringify(output, null, 2) }] }
}

async function promptSession(ctx: any, sessionID: string, prompt: any): Promise<any> {
  const payload: any = { sessionID, text: prompt.text }
  if (prompt.delivery) payload.delivery = prompt.delivery
  return unwrap(await ctx.session.prompt(payload))
}

async function workerCapabilities(ctx: any, helper: string, cwd: string, toolContext: any) {
  const caller = await resolveCaller(ctx, toolContext)
  const data = await runHelper(helper, "capabilities",
    { capabilities: capabilityFacts(ctx), caller }, cwd)
  return toolResult(data.report)
}

/** The read-only coordinator identity: the calling session and its root. */
async function coordinatorIdentity(ctx: any, toolContext: any) {
  const caller = await resolveCaller(ctx, toolContext)
  return toolResult({ schema: 1, session_id: caller.session_id, root: caller.location })
}

async function startWorker(ctx: any, helper: string, cwd: string, input: any, toolContext: any) {
  const request = normalizeRequest(input)
  const caller = await resolveCaller(ctx, toolContext)
  const guard = await acquireGuard(helper, cwd, input?.assignment ?? {}, caller.session_id)
  try {
    const capabilities = capabilityFacts(ctx)
    const model_facts = await modelFacts(ctx, (input?.assignment ?? {}).model)
    const plan = await runHelper(helper, "plan-start",
      { request, capabilities, caller, model_facts }, cwd)
    if (plan.action === "adopted") return toolResult(plan.envelope)

    const created = async (create: any) => {
      const session = unwrap(await ctx.session.create(create))
      const recorded = await runHelper(helper, "record-session", { request, session, caller }, cwd)
      const inbox = await promptSession(ctx, plan.session_id, recorded.prompt)
      const done = await runHelper(helper, "record-prompt", { request, inbox, caller }, cwd)
      return toolResult(done.envelope)
    }

    if (plan.action === "create") return await created(plan.create)

    // Reconcile a crash window from durable session and message state -- never from events.
    const session = await getSession(ctx, plan.session_id)
    const messages = session ? await readMessages(ctx, plan.session_id) : []
    const reconciled = await runHelper(helper, "reconcile",
      { request, session, messages, caller }, cwd)
    if (reconciled.action === "create") return await created(reconciled.create)
    if (reconciled.action === "prompt") {
      const inbox = await promptSession(ctx, plan.session_id, reconciled.prompt)
      const done = await runHelper(helper, "record-prompt", { request, inbox, caller }, cwd)
      return toolResult(done.envelope)
    }
    return toolResult(reconciled.envelope)
  } finally {
    await guard.release()
  }
}

async function workerStatus(ctx: any, helper: string, cwd: string, input: any, toolContext: any) {
  const request = normalizeAssignmentInput(input)
  const caller = await resolveCaller(ctx, toolContext)
  const plan = await runHelper(helper, "plan-status", { request, caller }, cwd)
  if (plan.action === "unknown") return toolResult(plan.envelope)
  const guard = await acquireGuard(helper, cwd, input?.assignment ?? {}, caller.session_id)
  try {
    const session = await getSession(ctx, plan.session_id)
    const messages = session ? await readMessages(ctx, plan.session_id) : []
    const status = await runHelper(helper, "interpret-status",
      { request, session, messages, caller }, cwd)
    return toolResult(status.envelope)
  } finally {
    await guard.release()
  }
}

async function interruptWorker(ctx: any, helper: string, cwd: string, input: any, toolContext: any) {
  const request = normalizeAssignmentInput(input)
  const caller = await resolveCaller(ctx, toolContext)
  const plan = await runHelper(helper, "plan-interrupt", { request, caller }, cwd)
  // An unknown or already-interrupted assignment answers its envelope before any native
  // access: only a journaled, admitted session may be interrupted.
  if (plan.action !== "interrupt") return toolResult(plan.envelope)
  const guard = await acquireGuard(helper, cwd, input?.assignment ?? {}, caller.session_id)
  try {
    const interrupt = unwrap(await ctx.session.interrupt({ sessionID: plan.session_id, resume: false }))
    const session = await getSession(ctx, plan.session_id)
    const envelope = await runHelper(helper, "interpret-interrupt",
      { request, interrupt, session, caller }, cwd)
    return toolResult(envelope.envelope)
  } finally {
    await guard.release()
  }
}

const ASSIGNMENT_SCHEMA = {
  type: "object",
  description: "The schema-1 pinned assignment (docs/opencode-parallel.md §4); validated by scripts/parallel_contract.py.",
}

const START_INPUT = {
  type: "object",
  properties: {
    assignment: ASSIGNMENT_SCHEMA,
    agent: { type: "string", description: "The pinned worker role (the session's explicit agent)." },
    prompt: {
      type: "object",
      properties: {
        text: { type: "string", description: "The pinned prompt the worker receives." },
        delivery: { type: "string", enum: ["steer", "queue"] },
      },
      required: ["text"],
      additionalProperties: false,
    },
    permissions: {
      type: "array",
      description: "The session permission ruleset to pin on the worker; omit for none.",
      items: {
        type: "object",
        properties: {
          action: { type: "string" },
          resource: { type: "string" },
          effect: { type: "string", enum: ["allow", "deny", "ask"] },
        },
        required: ["action", "resource", "effect"],
        additionalProperties: false,
      },
    },
  },
  required: ["assignment", "agent", "prompt"],
  additionalProperties: false,
}

const ASSIGNMENT_INPUT = {
  type: "object",
  properties: { assignment: ASSIGNMENT_SCHEMA },
  required: ["assignment"],
  additionalProperties: false,
}

const OUTPUT_SCHEMA = { type: "object" }

/**
 * Register the adapter's tools on a plugin context. The bridge calls this from its own
 * `setup`; the registration binds tools only -- no hooks, no event subscriptions, no
 * stop-gate or baseline wiring.
 */
export function registerParallel(ctx: any, options: ParallelOptions = {}): void {
  const helper = helperPath(options)
  const cwd = typeof ctx?.location?.directory === "string" ? ctx.location.directory : process.cwd()

  ctx.tool.transform((editor: any) => {
    editor.namespace({
      name: TOOL_NAMESPACE,
      description: "Parallel worker sessions: coordinator-only launch, durable status and interruption. Never schedules, claims or publishes.",
    })
    editor.add({
      name: "workerCapabilities",
      description:
        "Read-only parallel-worker capability report: the installed OpenCode version and the " +
        "session API surface the adapter needs. Call this before any startWorker. Coordinator " +
        "sessions only; creates nothing.",
      options: { namespace: TOOL_NAMESPACE, codemode: false },
      input: { type: "object", properties: {}, additionalProperties: false },
      output: OUTPUT_SCHEMA,
      execute: async (input: any, toolContext: any) =>
        workerCapabilities(ctx, helper, cwd, toolContext),
    })
    editor.add({
      name: "coordinatorIdentity",
      description:
        "Read-only coordinator identity: the calling session id and its root. Call this " +
        "before owner acquire and pass `session_id` as `coordinator_id` on every mutating " +
        "CLI request. Coordinator sessions only; creates nothing and takes no lock.",
      options: { namespace: TOOL_NAMESPACE, codemode: false },
      input: { type: "object", properties: {}, additionalProperties: false },
      output: OUTPUT_SCHEMA,
      execute: async (_input: any, toolContext: any) =>
        coordinatorIdentity(ctx, toolContext),
    })
    editor.add({
      name: "startWorker",
      description:
        "Start one pinned worker session for an already-owned assignment in its lifecycle " +
        "worker worktree: persists the launch intent, creates the session at the worker root " +
        "with the pinned agent/model/permissions, persists its id, then admits the pinned " +
        "prompt. Safe to call again after a crash: duplicates and crash windows reconcile " +
        "from durable state and never create a second session. Coordinator sessions only; " +
        "never schedules, claims or publishes.",
      options: { namespace: TOOL_NAMESPACE, codemode: false },
      input: START_INPUT,
      output: OUTPUT_SCHEMA,
      execute: async (input: any, toolContext: any) =>
        startWorker(ctx, helper, cwd, input, toolContext),
    })
    editor.add({
      name: "workerStatus",
      description:
        "Durable status and result recovery for one started worker: the native session state " +
        "plus the validated <artifact_root>/result.json. Never reads events. Idle and native " +
        "success are not completion; a missing, malformed or identity-mismatched result stays " +
        "non-successful, and an interrupted session wins over a contradictory result.",
      options: { namespace: TOOL_NAMESPACE, codemode: false },
      input: ASSIGNMENT_INPUT,
      output: OUTPUT_SCHEMA,
      execute: async (input: any, toolContext: any) =>
        workerStatus(ctx, helper, cwd, input, toolContext),
    })
    editor.add({
      name: "interruptWorker",
      description:
        "Interrupt the worker session recorded for an assignment and report the durable " +
        "post-interrupt state. Coordinator sessions only; never resumes, retries or publishes.",
      options: { namespace: TOOL_NAMESPACE, codemode: false },
      input: ASSIGNMENT_INPUT,
      output: OUTPUT_SCHEMA,
      execute: async (input: any, toolContext: any) =>
        interruptWorker(ctx, helper, cwd, input, toolContext),
    })
  })
}
