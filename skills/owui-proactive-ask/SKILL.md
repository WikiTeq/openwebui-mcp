---
name: owui-proactive-ask
description: Always consult the Open WebUI knowledge model through mcp__owui__ask before answering ANY user question. Use for every explicit or implicit question, including requests for advice, explanations, confirmation, analysis, recommendations, troubleshooting, and mixed question/action messages. Trigger even when the agent already knows the answer.
---

# Proactive OWUI Ask

Consult `mcp__owui__ask` before answering every user question.

## Required workflow

1. Detect all explicit and implicit questions in the user message
2. Call `mcp__owui__ask` before the final answer
3. Put the complete question and all relevant conversation context in `prompt` or `history`
4. Keep `use_tools: true` unless the user explicitly asks to disable remote tools
5. Use the configured default model unless the user requests a specific available model
6. Check the OWUI answer against local evidence, applicable instructions, and tool results
7. Give the user a concise, integrated answer. Do not copy incorrect or unsafe OWUI output

## Scope

Treat these as questions:

- Sentences ending in `?`
- Direct questions without a question mark
- Requests to explain, compare, assess, review, diagnose, recommend, identify, verify, or advise
- Requests phrased as commands when the user expects an informational answer
- Mixed messages that contain both an action request and a question
- Follow-up and meta questions about the agent, tools, or prior answers

Pure action requests with no informational answer expected do not require this skill. If uncertain, call `mcp__owui__ask`.

## Stateless server

OWUI calls are stateless. Supply relevant earlier exchanges through `history`:

```json
[
  {"role": "user", "content": "Earlier question"},
  {"role": "assistant", "content": "Earlier answer"}
]
```

Do not assume that OWUI remembers a previous call.

## Failure handling

If `mcp__owui__ask` is unavailable or fails:

1. Retry once when the failure can be transient
2. Continue with the best available answer when retrying does not help
3. State briefly that OWUI consultation failed

Never invent an OWUI answer or claim that the call succeeded when it did not.
