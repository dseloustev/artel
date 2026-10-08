/**
 * artel bridge for OpenCode 2.x.
 *
 * Adapts OpenCode's v2 plugin hooks onto artel's existing Python hook contracts
 * (stdin JSON in, stdout JSON out — hooks/README.md), so the hook layer stays
 * single-sourced. Mapping (details: docs/opencode.md):
 *
 *   tool.execute.before (edit|write|patch) -> hooks/sensitive_guard.py
 *       deny -> throw (v2's documented Tool.Error channel)
 *   tool.execute.before (edit|write|patch) -> hooks/spec_store_guard.py (after sensitive_guard.py)
 *       deny -> throw
 *   tool.execute.before (read) -> hooks/spec_store_guard.py (the Read hint for a swept image)
 *       deny -> throw (the read errors, naming `spec_store.py image fetch`)
 *   tool.execute.before (shell | any non-mutation tool whose name carries a platform token:
 *                       bitbucket / github / jira)             -> hooks/vcs_guard.py
 *       deny -> throw
 *   tool.execute.after  (edit|write|patch) -> hooks/fast_verify_post_edit.py
 *                       THEN hooks/knowledge_mirror.py (side effect only)
 *       findings -> appended to the tool result (v2 has no failure channel here)
 *   session.hook("context") -> hooks/using_artel.py — router + host status prepended to the
 *                       first user message in memory, on every model step
 *   session.created    -> hooks/session_baseline.py (child sessions skip)
 *   session.execution.succeeded -> hooks/stop_gate.py + hooks/verify_stop_gate.py; a block
 *                       re-prompts via ctx.session.prompt
 *   session.compaction.ended -> router cache invalidation (host status refreshes)
 *   session.deleted    -> cache cleanup
 *
 * `patch` carries no file path; its targets are read from the patch grammar's
 * `*** Add|Delete|Update File:` / `*** Move to:` headers. `edit`/`write` use v2's
 * `path` input field.
 *
 * The event stream is server-wide: session events are filtered to sessions whose own
 * location is this checkout (`session.created` carries the location; execution events
 * carry none, so the session is read once and cached).
 *
 * Everything is inert unless the project has .artel/config.json. Install root:
 * $ARTEL_ROOT or ~/.config/opencode/artel (scripts/install-opencode.sh).
 *
 * The default export is a plain object: v2 validates it for `id` + `setup`. The
 * `@opencode/plugin` import is type-only and erased — the package is not resolvable
 * from the plugin's install directory.
 */
import { spawn } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import type { Plugin } from "@opencode/plugin"

const CONFIG_HOME = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config")
const ARTEL_ROOT = process.env.ARTEL_ROOT || path.join(CONFIG_HOME, "opencode", "artel")

/** v2's file-mutating tools. `patch` is exposed only to GPT-family models. */
const MUTATION_TOOLS = new Set(["edit", "write", "patch"])
/** Platform tokens hooks/vcs_guard.py classifies a tool name by. The guard deliberately does
 * NOT test for Claude Code's `mcp__` prefix — OpenCode names MCP tools without it. */
const PLATFORM_TOKENS = ["bitbucket", "github", "jira"]
/** Bridge-side fail-safe on top of the hooks' own consecutive-block caps (5 and 2). */
const MAX_STOP_BLOCKS = 10
/** Marker from using_artel.py's header — proves a message already carries the router. */
const ROUTER_MARKER = "This repository is configured for artel"

const ROUTER_NOTE =
  "\n\nOpenCode note: the routing tables above name skills as `/artel:<name>`. " +
  "On this host they are the skills `artel-<name>` (TUI commands `/artel-<name>`), " +
  "loaded with the `skill` tool; agents are dispatched with the `subagent` tool as " +
  "`artel-<name>`. `/ast-index:*` commands are not available unless that plugin is " +
  "installed — otherwise run the `ast-index` CLI directly."

type HookResult = { code: number; stdout: string; stderr: string }

function hasArtelConfig(directory: string): boolean {
  try {
    return fs.existsSync(path.join(directory, ".artel", "config.json"))
  } catch {
    return false
  }
}

function runHook(script: string, payload: unknown, cwd: string, timeoutMs: number): Promise<HookResult> {
  return new Promise((resolve) => {
    let stdout = ""
    let stderr = ""
    const child = spawn("python3", [path.join(ARTEL_ROOT, "hooks", script)], { cwd })
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs)
    child.stdout.on("data", (chunk) => (stdout += chunk))
    child.stderr.on("data", (chunk) => (stderr += chunk))
    // A missing python3 (ENOENT) or a hook that exits before reading its input can
    // reject the pipe — swallow it; the exit code / stderr already carry the failure.
    child.stdin.on("error", () => {})
    child.on("error", (error) => {
      clearTimeout(timer)
      resolve({ code: 2, stdout, stderr: String(error) })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve({ code: code ?? 2, stdout, stderr })
    })
    child.stdin.write(JSON.stringify(payload))
    child.stdin.end()
  })
}

/** First JSON object on stdout (hooks print one line of JSON, or nothing). */
function firstJson(stdout: string): any | null {
  for (const line of stdout.split("\n")) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      return JSON.parse(trimmed)
    } catch {
      // not JSON — keep scanning
    }
  }
  return null
}

/** A non-mutation tool whose name carries a platform token — the OpenCode-side reading of
 * the guard's rule ("the tool name names a platform"). `shell` is bound separately: it is
 * matched by name, and its command argument has to travel with the payload. */
function isPlatformTool(tool: string): boolean {
  if (MUTATION_TOOLS.has(tool) || tool === "read" || tool === "shell") return false
  const lower = tool.toLowerCase()
  return PLATFORM_TOKENS.some((token) => lower.includes(token))
}

/** v2 `edit`/`write` carry `path`; the fallbacks cover older spellings. */
function toolPath(input: any): string | undefined {
  const value = input?.path ?? input?.filePath ?? input?.file_path
  return typeof value === "string" && value ? value : undefined
}

/** Paths a v2 `patch` call writes, from the patch grammar's file headers. */
function patchPaths(patchText: string): string[] {
  const paths: string[] = []
  for (const line of String(patchText || "").split("\n")) {
    const file = line.match(/^\*\*\* (?:Add|Delete|Update) File: (.+)$/)
    const move = line.match(/^\*\*\* Move to: (.+)$/)
    if (file) paths.push(file[1].trim())
    if (move) paths.push(move[1].trim())
  }
  return [...new Set(paths.filter(Boolean))]
}

/** The file paths a mutation tool call touches; [] when none can be resolved. */
function mutationPaths(tool: string, input: any): string[] {
  if (tool === "patch") return patchPaths(input?.patchText)
  const one = toolPath(input)
  return one ? [one] : []
}

/** artel file path -> the Claude Code hook payload shape. */
function claudePayload(sessionID: string, directory: string, claudeName: string, filePath: string) {
  return { session_id: sessionID, cwd: directory, tool_name: claudeName, tool_input: { file_path: filePath } }
}

function claudeToolName(tool: string): string {
  return tool === "write" ? "Write" : "Edit"
}

/** Findings -> the tool result the model sees. v2 has no failure channel for
 * execute.after, so mutation is the documented path. */
function appendFindings(event: any, findings: string): void {
  const result = event?.result
  if (!result || event.status !== "completed") return
  if (typeof result.content === "string") {
    result.content = result.content + "\n\n" + findings
  } else if (Array.isArray(result.content)) {
    result.content = [...result.content, { type: "text", text: findings }]
  } else {
    result.content = findings
  }
  result.metadata = { ...(result.metadata ?? {}), artel_findings: findings }
}

/** Session id -> CONSECUTIVE stop-gate blocks. Reset when both gates pass. */
const stopBlocks = new Map<string, number>()
/** Session id -> router context. Only successful lookups are cached. */
const routerCache = new Map<string, string>()
/** Subagent child sessions — they get no router and no stop gate. */
const childSessions = new Set<string>()
/** Sessions with a stop-gate run in flight, so two runs never overlap. */
const stopRuns = new Set<string>()
/** Session id -> its own location directory. The event stream is server-wide, so every
 * event-driven hook checks this before touching this location's state. */
const sessionLocations = new Map<string, string>()

const setup: Plugin["setup"] = async (ctx) => {
  const directory = ctx.location.directory
  const controller = new AbortController()

  /** The session's own location directory, or '' when it cannot be resolved. Cached:
   * `session.created` carries the location, execution events do not. A failed lookup is
   * not cached, so a transient error never pins a session as foreign. */
  const sessionDirectory = async (sessionID: string): Promise<string> => {
    const known = sessionLocations.get(sessionID)
    if (known !== undefined) return known
    try {
      const session: any = await ctx.session.get({ sessionID })
      const resolved = String((session?.data ?? session)?.location?.directory ?? "")
      sessionLocations.set(sessionID, resolved)
      return resolved
    } catch {
      return ""
    }
  }

  await ctx.tool.hook("execute.before", async (event) => {
    if (!hasArtelConfig(directory)) return

    if (event.tool === "shell" || isPlatformTool(event.tool)) {
      const payload = {
        session_id: event.sessionID,
        cwd: directory,
        tool_name: event.tool === "shell" ? "Bash" : event.tool,
        tool_input: event.tool === "shell" ? { command: (event.input as any)?.command ?? "" } : {},
      }
      const result = await runHook("vcs_guard.py", payload, directory, 10_000)
      const decision = firstJson(result.stdout)?.hookSpecificOutput
      if (decision?.permissionDecision === "deny") {
        throw new Error(
          `artel vcs guard: ${decision.permissionDecisionReason ?? "this platform is not this project's home"}`,
        )
      }
      return
    }

    if (event.tool === "read") {
      const filePath = toolPath(event.input)
      if (!filePath) return
      const hint = await runHook(
        "spec_store_guard.py",
        claudePayload(event.sessionID, directory, "Read", filePath),
        directory,
        10_000,
      )
      const hintDecision = firstJson(hint.stdout)?.hookSpecificOutput
      if (hintDecision?.permissionDecision === "deny") {
        throw new Error(
          `artel spec-store guard: ${hintDecision.permissionDecisionReason ?? "images are stored in kartoteka"}`,
        )
      }
      return
    }

    if (!MUTATION_TOOLS.has(event.tool)) return
    for (const filePath of mutationPaths(event.tool, event.input)) {
      const payload = claudePayload(event.sessionID, directory, claudeToolName(event.tool), filePath)
      const result = await runHook("sensitive_guard.py", payload, directory, 30_000)
      const decision = firstJson(result.stdout)?.hookSpecificOutput
      if (decision?.permissionDecision === "deny") {
        throw new Error(
          `artel sensitive-path guard: ${decision.permissionDecisionReason ?? "this path is protected"}`,
        )
      }
      const store = await runHook("spec_store_guard.py", payload, directory, 10_000)
      const storeDecision = firstJson(store.stdout)?.hookSpecificOutput
      if (storeDecision?.permissionDecision === "deny") {
        throw new Error(
          `artel spec-store guard: ${storeDecision.permissionDecisionReason ?? "spec documents live in kartoteka"}`,
        )
      }
    }
  })

  await ctx.tool.hook("execute.after", async (event) => {
    if (!hasArtelConfig(directory) || !MUTATION_TOOLS.has(event.tool)) return
    // Verify first, mirror second (hooks.json's order): nothing throws anymore, so the
    // ordering hack the v1 bridge needed is gone.
    for (const filePath of mutationPaths(event.tool, event.input)) {
      const payload = claudePayload(event.sessionID, directory, claudeToolName(event.tool), filePath)
      const result = await runHook("fast_verify_post_edit.py", payload, directory, 150_000)
      await runHook("knowledge_mirror.py", payload, directory, 15_000)
      const findings = firstJson(result.stdout)?.hookSpecificOutput?.additionalContext
      if (findings) appendFindings(event, findings)
    }
  })

  await ctx.session.hook("context", async (event) => {
    if (!hasArtelConfig(directory)) return
    const firstUser: any = (event.messages ?? []).find((message: any) => message.role === "user")
    const sessionID = event.sessionID
    if (!firstUser || !Array.isArray(firstUser.content) || !sessionID) return
    // Subagent child sessions get no router: routing is the main session's job.
    if (childSessions.has(sessionID)) return
    if (firstUser.content.some((part: any) => part?.type === "text" && part.text?.includes(ROUTER_MARKER))) return
    let context = routerCache.get(sessionID)
    if (context === undefined) {
      const result = await runHook("using_artel.py", { session_id: sessionID, cwd: directory }, directory, 10_000)
      context = firstJson(result.stdout)?.hookSpecificOutput?.additionalContext
      if (context) {
        routerCache.set(sessionID, context)
      } else {
        // With .artel/config.json present the hook ALWAYS emits context, so no output means
        // it failed. Don't cache the failure: retry on the next model step.
        const diagnostic = result.stderr.trim()
        if (diagnostic) console.warn("artel: using_artel produced no router context: " + diagnostic)
        return
      }
    }
    firstUser.content.unshift({ type: "text", text: context + ROUTER_NOTE })
  })

  const runStopGate = async (sessionID: string) => {
    // The stream is server-wide and execution events carry no location: a session from
    // another checkout must never run this location's gates.
    if ((await sessionDirectory(sessionID)) !== directory) return
    for (const script of ["stop_gate.py", "verify_stop_gate.py"]) {
      const result = await runHook(script, { session_id: sessionID, cwd: directory }, directory, 300_000)
      const decision = firstJson(result.stdout)
      if (decision?.decision !== "block" || !decision?.reason) {
        // The gates' pass-through warnings (cap reached, verify env error) must not become
        // silent here; v2 has no app-log client, so console carries them.
        const warning = decision?.systemMessage || result.stderr.trim()
        if (warning) console.warn("artel: " + warning)
        continue
      }
      const blocks = (stopBlocks.get(sessionID) ?? 0) + 1
      stopBlocks.set(sessionID, blocks)
      if (blocks > MAX_STOP_BLOCKS) {
        console.warn(`artel: stop-gate block cap (${MAX_STOP_BLOCKS}) reached for session ${sessionID}`)
        return
      }
      try {
        await ctx.session.prompt({
          sessionID,
          text:
            `artel stop gate blocked this stop:\n${decision.reason}\n` +
            "Address the findings, then finish again.",
        })
      } catch (error) {
        console.warn("artel: stop-gate re-prompt failed: " + String(error))
      }
      return
    }
    // Both gates passed — the consecutive-block run (if any) is over.
    stopBlocks.delete(sessionID)
  }

  // The subscription is pull-based and awaits this handler, so a 300 s hook awaited inline
  // would stall every other event: gate and baseline runs are fire-and-forget, and the
  // per-session in-flight set keeps two gate runs from overlapping.
  void (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        if (!hasArtelConfig(directory)) continue
        const data: any = (event as any).data ?? {}
        const sessionID: string | undefined = data.sessionID

        if (event.type === "session.created") {
          // The session's own location rides on the event; cache it, and act only when it
          // is this checkout (the stream is server-wide).
          const sessionDir = String(data.location?.directory ?? "")
          if (sessionID) sessionLocations.set(sessionID, sessionDir)
          if (sessionDir !== directory) continue
          if (data.parentID) {
            if (sessionID) childSessions.add(sessionID)
            continue
          }
          if (sessionID) {
            void runHook("session_baseline.py", { session_id: sessionID, cwd: directory }, directory, 120_000)
          }
          continue
        }

        if (event.type === "session.moved") {
          // A move changes a session's location; a stale cache entry would misroute it.
          if (sessionID) sessionLocations.set(sessionID, String(data.location?.directory ?? ""))
          continue
        }

        if (event.type === "session.compaction.ended") {
          if (sessionID) routerCache.delete(sessionID)
          continue
        }

        if (event.type === "session.deleted") {
          if (sessionID) {
            routerCache.delete(sessionID)
            childSessions.delete(sessionID)
            stopBlocks.delete(sessionID)
            sessionLocations.delete(sessionID)
          }
          continue
        }

        if (event.type === "session.execution.succeeded") {
          if (!sessionID || childSessions.has(sessionID) || stopRuns.has(sessionID)) continue
          stopRuns.add(sessionID)
          void runStopGate(sessionID).finally(() => stopRuns.delete(sessionID))
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) console.warn("artel: event subscription ended: " + String(error))
    }
  })()

  return () => controller.abort()
}

const plugin: Plugin = { id: "artel", setup }
export default plugin
