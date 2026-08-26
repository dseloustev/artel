/**
 * artel bridge for OpenCode.
 *
 * Adapts OpenCode's plugin events onto artel's existing Python hook contracts
 * (stdin JSON in, stdout JSON out — hooks/README.md), so the hook layer stays
 * single-sourced. Mapping (details: docs/opencode.md):
 *
 *   tool.execute.before (edit|write|apply_patch) -> hooks/sensitive_guard.py
 *       deny               -> throw (OpenCode's way to deny a tool call)
 *   tool.execute.after  (edit|write|apply_patch) -> hooks/fast_verify_post_edit.py
 *                    + hooks/knowledge_mirror.py (side effect only)
 *       findings          -> throw (the model sees them as the tool's error)
 *   session.created    -> hooks/session_baseline.py + hooks/using_artel.py   (Task 5)
 *   session.idle       -> hooks/stop_gate.py + hooks/verify_stop_gate.py     (Task 5)
 *
 * Everything is inert unless the project has .artel/config.json. Install root:
 * $ARTEL_ROOT or ~/.config/opencode/artel (scripts/install-opencode.sh).
 */
import { spawn } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import type { Plugin } from "@opencode-ai/plugin"

const CONFIG_HOME = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config")
export const ARTEL_ROOT = process.env.ARTEL_ROOT || path.join(CONFIG_HOME, "opencode", "artel")

const EDIT_TOOLS = new Set(["edit", "write", "apply_patch"])
// Bridge-side fail-safe on top of the hooks' own consecutive-block caps (5 and 2).
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

// (`client` and `idleBlocks` are unused until Task 5 — the linter may warn; that is
// deliberate scaffolding, removed noise is worse than a warning.)
const idleBlocks = new Map<string, number>()

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
      await runHook("knowledge_mirror.py", payload, directory, 15_000)
      const result = await runHook("fast_verify_post_edit.py", payload, directory, 150_000)
      const context = firstJson(result.stdout)?.hookSpecificOutput?.additionalContext
      if (context) throw new Error(`artel fast-verify findings:\n${context}`)
    },
  }
}
