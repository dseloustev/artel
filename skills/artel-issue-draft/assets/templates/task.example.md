# A worked task example

One input, and the description [task.template.md](task.template.md) renders from it. Read it when
the template's rules alone leave a section's tone or granularity open.

## Input

> Add CSV export to the transaction-history screen. Platform — All. Design file:
> https://figma.com/design/abc. There is no endpoint yet; the backend part comes later.

## Output

```text
h2. Task description:
Add CSV export to the transaction-history screen: a control in the screen header that writes the
rows the current filter shows to a file on the device.

h2. Technical details:
The export runs on the client from the rows already loaded; no request is added. The file is named
`transactions-<yyyy-mm-dd>.csv`, and the delimiter is a comma with the header row written first.
The backend export is out of scope and tracked separately.

|*Platform*|All|
|*URLs*| |
|*Figma*|Export control: https://figma.com/design/abc|
|*Notion*| |

h2. AC:
- The control is hidden while the filter returns no rows.
- The file carries exactly the columns the list shows, in the list's order.
- A second export overwrites the day's file instead of appending to it.

h2. Additional:
- Check a list longer than 1000 rows before release.
```
