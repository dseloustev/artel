# A worked epic example

One input, and the description [epic.template.md](epic.template.md) renders from it. Read it when
the template's rules alone leave a section's tone or granularity open.

## Input

> An epic for a card the user holds inside the product: it is issued from the wallet, funded from
> the wallet balance, and usable to pay. AC: issue, top up, freeze, see the details. Platform —
> All.

## Output

```text
A card the user holds inside the product: issued from the wallet, funded from its balance, and
usable for payments at checkout.
----
| As [user role] | I as User |
| I want [what they want to do] | Want a card funded from my wallet balance |
| So that [what value it brings] | So that I can pay without moving funds out of the product |
| Platform | All |
| Notion | |
| Figma | |

h2. Acceptance Criteria:
- A card can be issued once the account is verified, and its number and expiry become visible.
- Funds move from the wallet balance to the card, and any fee is named before confirmation.
- A card can be frozen and unfrozen, and a frozen card declines a payment.
- A card's details can be revealed after an authentication step.

h2. Additional:
- Card operations depend on the account-verification state; the endpoint does not exist yet.
```
