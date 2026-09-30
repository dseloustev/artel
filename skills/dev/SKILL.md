---
name: dev
description: "Alias of /artel:feature-development --head=lean, kept for one release: prints one line and hands its arguments on. Goes away in 0.26.0."
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates]"
---

Print this line, exactly:

    /artel:dev is now /artel:feature-development --head=lean and goes away in 0.26.0.

Then invoke `Skill: feature-development` with `$ARGUMENTS --head=lean`, and follow it. Nothing
else is this skill's: it has no gate, no question and no file of its own.
