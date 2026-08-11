# Concierge, Operator, Auditor

The three LLM roles a request passes through. The names stay as they are -
they are woven through `schemas.py` (`OperatorDecision`, `OperatorAction`) and
the recorded scenario fixtures depend on them. What changed is the *user-facing*
wording: the console no longer names them at a first-time user, and says
"understand requests, do the work, and check the result" instead.

This document is for people reading the code. The mapping:

| Role | User-facing phrasing | What it owns |
|---|---|---|
| **Concierge** | *understands requests* | Classifies the incoming message, asks for clarification, and composes the chat reply |
| **Operator** | *does the work* | The tool-calling loop: decides the next action, runs it under policy, repeats |
| **Auditor** | *checks the result* | Verifies the work against the objective before it is reported as done |

## The path a request takes

```mermaid
flowchart TD
    IN["Message arrives<br/>(web chat, Telegram, WhatsApp)"] --> C

    C{"Concierge<br/><i>understands</i>"}
    C -->|"just a question"| REPLY["Reply directly"]
    C -->|"needs clarifying"| ASK["Ask the user"]
    C -->|"is a task"| OP

    OP["Operator<br/><i>does the work</i>"]
    OP --> DECIDE{"decide()<br/>next action"}
    DECIDE -->|"call a tool"| POLICY
    DECIDE -->|"done"| AUD

    POLICY{"Policy engine"}
    POLICY -->|"allowed"| RUN["Run the tool"]
    POLICY -->|"needs approval"| APPROVE["Wait for the user"]
    POLICY -->|"denied"| DENIED["Refuse, and say why"]
    APPROVE -->|"approved"| RUN
    RUN --> DECIDE

    AUD{"Auditor<br/><i>checks the result</i>"}
    AUD -->|"meets the objective"| DONE["Report the result"]
    AUD -->|"falls short"| OP

    REPLY --> OUT["Answer goes back<br/>on the channel it came from"]
    ASK --> OUT
    DONE --> OUT
    DENIED --> OUT
```

The loop between Operator and Auditor is the important part: the Auditor can
send work back, which is why a task can run several rounds before it reports.
Retries are bounded - see `orchestration/worker.py`.

## Where each one lives

```mermaid
flowchart LR
    subgraph channels["channels/"]
        TG["telegram.py"]
        WA["whatsapp.py"]
        WEB["web chat<br/>(admin API)"]
    end

    subgraph brain["LLM roles"]
        CON["Concierge<br/>llm/classifier.py"]
        OPR["Operator<br/>orchestration/worker.py"]
        AUD["Auditor<br/>orchestration/worker.py"]
    end

    subgraph exec["Execution"]
        POL["policy/<br/>capabilities, risk, scopes"]
        TOOLS["tools/<br/>browser, filesystem, code, desktop"]
    end

    TG --> CON
    WA --> CON
    WEB --> CON
    CON --> OPR
    OPR --> POL
    POL --> TOOLS
    TOOLS --> OPR
    OPR --> AUD
    AUD --> OPR
```

All three roles share one model profile by default. `llm/config` supports a
separate `major_profile` for heavier work and a `fallback_profile` used when
the primary endpoint is unreachable - connection errors, timeouts, or HTTP 5xx
only. A 4xx or a bad structured response is *not* failed over, because it would
fail the same way against the fallback and switching models would hide the real
problem (`llm/providers.py::_is_unavailability`).

## Why the names are not being changed

Renaming would touch `OperatorDecision`, `OperatorAction`, and the schema
fields the sixteen recorded scenario fixtures assert against. That is a large,
risky diff whose entire benefit - a first-time user not meeting internal
jargon - is already delivered by changing the words in the console.

If a short user-facing name is ever needed for a diagram, the suggestion on
file is **Intake / Runner / Reviewer**.
