/**
 * artel bridge for OpenCode.
 *
 * Adapts OpenCode's plugin events onto artel's existing Python hook contracts
 * (stdin JSON in, stdout JSON out — hooks/README.md), so the hook layer stays
 * single-sourced. Mapping (details: docs/opencode.md):
 *
 *   tool.execute.before (edit|write|apply_patch) -> hooks/sensitive_guard.py
 *       deny               -> throw (OpenCode's way to deny a tool call)
 *   tool.execute.after  (edit|write|apply_patch) -> hooks/knowledge_mirror.py (side
 *                       effect only) THEN hooks/fast_verify_post_edit.py
 *       findings          -> throw (the model sees them as the tool's error)
 *       NOTE: this is the reverse of hooks.json's order, deliberately — findings
 *       leave this handler by throwing, and a throw would skip a mirror queued
 *       behind it. Claude Code runs both regardless, so the observable outcome
 *       matches; here the order is what makes it match.
 *   session.created    -> hooks/session_baseline.py                           (Task 5)
 *   session.idle       -> hooks/stop_gate.py + hooks/verify_stop_gate.py      (Task 5)
 *   messages.transform -> hooks/using_artel.py — router + host status prepended
 *                         to the first user message on every model step (Task 8)
 *
 * Everything is inert unless the project has .artel/config.json. Install root:
 * $ARTEL_ROOT or ~/.config/opencode/artel (scripts/install-opencode.sh).
 *
 * NOTE: never add a non-function named export to this module — OpenCode's plugin
 * loader requires every export to be a function and rejects the whole plugin
 * otherwise ("Plugin export is not a function").
 */
import { spawn } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import type { Plugin } from "@opencode-ai/plugin"

const CONFIG_HOME = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config")
// NOT exported on purpose: OpenCode's legacy plugin loader requires every module
// export to be a function (or {server: fn}); a stray string export makes the
// whole plugin fail with "Plugin export is not a function".
const ARTEL_ROOT = process.env.ARTEL_ROOT || path.join(CONFIG_HOME, "opencode", "artel")

const EDIT_TOOLS = new Set(["edit", "write", "apply_patch"])
// Bridge-side fail-safe on top of the hooks' own consecutive-block caps (5 and 2).
// Bounds one runaway block/re-prompt loop, not the session's lifetime: the counter
// resets on a clean stop (see session.idle) and is dropped with the session.
const MAX_IDLE_BLOCKS = 10

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

/** Session id from an event's properties; upstream does not document the
 * shape, so try the known candidates (verified in the E2E smoke). */
function sessionId(properties: unknown): string | undefined {
  const props = (properties ?? {}) as Record<string, any>
  return props.info?.id ?? props.sessionID ?? props.session_id ?? undefined
}

/** OpenCode edit-tool args -> the Claude hook payload shape. */
function claudeEditPayload(sessionID: string, directory: string, tool: string, args: any) {
  const file_path = args?.filePath ?? args?.file_path
  return file_path
    ? { session_id: sessionID, cwd: directory, tool_name: tool, tool_input: { file_path } }
    : null
}

/** Session id -> CONSECUTIVE idle blocks. Reset the moment a stop passes both gates,
 * so unrelated blocks spread across a long session never add up to MAX_IDLE_BLOCKS and
 * retire the gate; dropped when the session is deleted. */
const idleBlocks = new Map<string, number>()
/** Session id -> router context. Only successful lookups are cached: a failed
 * hook run leaves NO entry, so the next model step retries (the hook is a fast
 * local call — this self-heals transient failures instead of latching them). */
const routerCache = new Map<string, string>()
/** Subagent child sessions (task tool) — they get no router. */
const childSessions = new Set<string>()

/** Marker from using_artel.py's header — proves a message already carries the router. */
const ROUTER_MARKER = "This repository is configured for artel"

export const ArtelPlugin: Plugin = async ({ client, directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (!EDIT_TOOLS.has(input.tool) || !hasArtelConfig(directory)) return
      const payload = claudeEditPayload(input.sessionID, directory, input.tool, output.args)
      if (!payload) return
      const result = await runHook("sensitive_guard.py", payload, directory, 30_000)
      const decision = firstJson(result.stdout)?.hookSpecificOutput
      if (decision?.permissionDecision === "deny") {
        throw new Error(
          `artel sensitive-path guard: ${decision.permissionDecisionReason ?? "this path is protected"}`,
        )
      }
    },

    "tool.execute.after": async (input) => {
      if (!EDIT_TOOLS.has(input.tool) || !hasArtelConfig(directory)) return
      const payload = claudeEditPayload(input.sessionID, directory, input.tool, input.args)
      if (!payload) return
      // Mirror first, verify second — the reverse of hooks.json, on purpose: the verify
      // findings leave this handler by throwing, which would skip a mirror queued behind
      // them. Claude Code runs both regardless; ordering is how that is reproduced here.
      await runHook("knowledge_mirror.py", payload, directory, 15_000)
      const result = await runHook("fast_verify_post_edit.py", payload, directory, 150_000)
      const context = firstJson(result.stdout)?.hookSpecificOutput?.additionalContext
      if (context) throw new Error(`artel fast-verify findings:\n${context}`)
    },

    // Router injection (Task 8). Claude Code injects the router as SessionStart
    // context before the first prompt; OpenCode sessions only come into being
    // WITH their first prompt, so there is no pre-prompt moment. Injecting via
    // client.session.prompt({noReply}) races that prompt and leaves the router
    // as an unanswered trailing user message — the loop then burns a whole turn
    // acknowledging it (and `opencode run` prints that acknowledgment instead
    // of the real answer). Prepending to the first user message on every model
    // step (in-memory, like the superpowers plugin does) avoids both: the model
    // sees the router together with the first prompt, and compaction cannot
    // drop it.
    "experimental.chat.messages.transform": async (_input, output) => {
      if (!hasArtelConfig(directory)) return
      const messages = output.messages ?? []
      const firstUser = messages.find((m) => m.info?.role === "user")
      const sessionID = firstUser?.info?.sessionID ?? messages[0]?.info?.sessionID
      if (!firstUser || !firstUser.parts?.length || !sessionID) return
      // Subagent child sessions get no router: routing is the main session's job
      // (the router's own <SUBAGENT-STOP> says the same).
      if (childSessions.has(sessionID)) return
      if (firstUser.parts.some((p) => (p as any).type === "text" && (p as any).text?.includes(ROUTER_MARKER))) return
      let context = routerCache.get(sessionID)
      if (context === undefined) {
        const result = await runHook("using_artel.py", { session_id: sessionID, cwd: directory }, directory, 10_000)
        context = firstJson(result.stdout)?.hookSpecificOutput?.additionalContext
        if (context) {
          routerCache.set(sessionID, context)
        } else {
          // With .artel/config.json present the hook ALWAYS emits context, so no
          // output means it failed (timeout, spawn error, internal exception —
          // details on stderr). Don't cache the failure: retry on the next model
          // step, but mirror the diagnostic so the missing router stays visible.
          const diagnostic = result.stderr.trim()
          if (diagnostic) {
            await client.app.log({
              body: {
                service: "artel",
                level: "warn",
                message: "using_artel produced no router context: " + diagnostic,
              },
            })
          }
          return
        }
      }
      // The injected router body is the canonical (Claude-dialect) text —
      // append the OpenCode reading of its names.
      const note =
        "\n\nOpenCode note: the routing tables above name skills as `/artel:<name>`. " +
        "On this host they are the skills `artel-<name>` (TUI commands `/artel-<name>`), " +
        "loaded with the `skill` tool; agents are dispatched with the `task` tool as " +
        "`artel-<name>`. `/ast-index:*` commands are not available unless that plugin is " +
        "installed — otherwise run the `ast-index` CLI directly."
      const ref = firstUser.parts[0]
      firstUser.parts.unshift({ ...ref, type: "text", text: context + note })
    },

    event: async ({ event }) => {
      if (!hasArtelConfig(directory)) return
      const properties = (event as any).properties ?? {}
      const id = sessionId(properties)

      if (event.type === "session.created") {
        // Subagent child sessions get no baseline/router (see the transform hook).
        if (properties.info?.parentID) {
          if (id) childSessions.add(id)
          return
        }
        if (!id) return
        await runHook("session_baseline.py", { session_id: id, cwd: directory }, directory, 120_000)
        return
      }

      if (event.type === "session.compacted") {
        // Claude Code re-fires SessionStart(compact); here the transform hook
        // recomputes instead — drop the cache so host status refreshes next step.
        if (id) routerCache.delete(id)
        return
      }

      if (event.type === "session.deleted") {
        // Bounds both maps in long-lived TUI processes; also drops a deleted
        // session's no-router marker.
        if (id) {
          routerCache.delete(id)
          childSessions.delete(id)
          idleBlocks.delete(id)
        }
        return
      }

      if (event.type === "session.idle") {
        if (!id) return
        // Claude Code runs stop_gate.py then verify_stop_gate.py on Stop; mirror the
        // order. A block decision re-prompts the session (OpenCode's idle event is the
        // closest thing to a Stop hook, and it cannot hard-block).
        for (const script of ["stop_gate.py", "verify_stop_gate.py"]) {
          const result = await runHook(script, { session_id: id, cwd: directory }, directory, 300_000)
          const decision = firstJson(result.stdout)
          if (decision?.decision !== "block" || !decision?.reason) {
            // Claude Code shows a hook's systemMessage / stderr to the user — the
            // gates' pass-through warnings (cap reached, verify env error) must not
            // become silent here. Mirror them into the app log.
            const warning = decision?.systemMessage || result.stderr.trim()
            if (warning) {
              await client.app.log({ body: { service: "artel", level: "warn", message: warning } })
            }
            continue
          }
          const blocks = (idleBlocks.get(id) ?? 0) + 1
          idleBlocks.set(id, blocks)
          if (blocks > MAX_IDLE_BLOCKS) {
            await client.app.log({
              body: {
                service: "artel",
                level: "warn",
                message: `stop-gate block cap (${MAX_IDLE_BLOCKS}) reached for session ${id}`,
              },
            })
            return
          }
          await client.session.prompt({
            path: { id },
            body: {
              parts: [{
                type: "text",
                text:
                  `artel stop gate blocked this stop:\n${decision.reason}\n` +
                  "Address the findings, then finish again.",
              }],
            },
          })
          return
        }
        // Both gates passed — the consecutive-block run (if any) is over. Without this
        // the counter only ever grows, and MAX_IDLE_BLOCKS eventually retires the stop
        // gate for the rest of a long-lived TUI session.
        idleBlocks.delete(id)
      }
    },
  }
}
