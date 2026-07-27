# hooks/

Quality gates: `hooks.json` (hook registration, `${CLAUDE_PLUGIN_ROOT}` paths) plus the Python
scripts — session baseline, fast per-edit verify, latching stop gate, sensitive-path guard.
Requires `python3` on the host.

Empty until [porting-plan Phase 5](../docs/porting-plan.md#phase-5--hooks-and-gates). Verify
commands are read from host config, never hardcoded.
