# First run, screenshot by screenshot

Captured from a **containerised install with nothing pre-configured** - the
state a brand-new user is actually in. Regenerate them with:

```
docker build -t ybm-control:test .
docker run -d --name ybm-test -p 127.0.0.1:8899:8765 \
  -e AGENT_ADMIN_TOKEN=<anything> ybm-control:test
# then walk http://127.0.0.1:8899/admin?token=<the same> with Playwright
```

| Shot | What it shows |
|---|---|
| `01-first-screen-pick-a-model` | Step 1. Detection result stated up front, presets in plain language, consequence of skipping |
| `02-bring-your-own-key` | The provider picker - 13 providers, key verified before it is saved |
| `03-connect-a-channel` | Step 2. Every way to reach YBM, with live status; planned channels shown greyed |
| `04-telegram-guided` | The three-step Telegram flow: BotFather → verify token → send a message to link |
| `05-done` | Ends on a suggested first message rather than an empty chat box |
| `06-chat` | The console, with the "no model configured" banner when setup was skipped |
| `07-tasks`, `08-settings`, `09-tools` | The rest of the console in its fresh-install state |

## What this pass changed

**The wizard was unskippable.** It called itself "skippable at every step",
but `showWizard` was driven purely by `onboarding_complete`, which stays false
when you skip - so skipping re-rendered the wizard forever and the console was
unreachable. "Start chatting" did nothing. A user who wanted to look around
before configuring anything was stuck with no way forward and no way out. Now a
skip really skips, and the console carries a non-dismissible banner naming the
consequence and offering the way back.

**The channel step was one hardcoded toggle.** "Also enable Telegram" implied
Telegram was the only thing that would ever exist, and web chat - the thing
already working - had no visible status. It is now a grid fed by a backend
catalog: web chat reads *Connected*, Telegram has a *Connect* button, WhatsApp
says where to go, and Discord/Slack/Signal/Matrix/Email are listed as coming
soon. Adding a channel is a row in `channels/catalog.py`.

**"Pick a face"** became **"Where can you reach it?"**, and the card gained a
product mark, a step indicator, and enough width to stop the content floating
in empty space.
