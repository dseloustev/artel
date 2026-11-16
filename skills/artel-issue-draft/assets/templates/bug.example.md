# A worked bug example

One input, and the description [bug.template.md](bug.template.md) renders from it. Read it when
the template's rules alone leave a section's tone or granularity open.

## Input

> On iOS 18.1, build 1.4.2 (88), the Receive screen shows an empty area instead of the QR code.
> Steps: open the app, open Receive. It should show a QR code with the current address. Nothing in
> the logs, no error on screen.

## Output

```text
The QR code does not appear on the Receive screen — the code area stays empty.
----

h2. Environment:
iPhone 15 / iOS 18.1 / 1.4.2 (88)

h2. Steps to reproduce:
# Open the app
# Open the Receive screen

h2. Expected result:
A QR code carrying the current receive address is shown.

h2. Actual result:
The code area stays empty. No error is shown on screen and nothing is logged.
```
