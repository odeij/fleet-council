# Agent Council: several models argue, one chairs

The council fans the same question out to several AI CLIs in parallel (here: OpenAI Codex pinned to a GPT model, and
Claude), collects their independent answers, and the host agent (Claude Code) synthesizes a final recommendation as
chairman: where they agree, where they disagree, and why it picks one side.

It runs on the CLIs you are already logged into, so there are no extra API keys or per-call API billing.

## Install

The council scripts are the open-source **agent-council** skill by Team Attention
(https://github.com/team-attention/agent-council), inspired by Karpathy's
[llm-council](https://github.com/karpathy/llm-council). They are not vendored here; install them from upstream:

```bash
npx github:team-attention/agent-council --target claude     # or install as a Claude Code plugin (see upstream README)
cp council.config.yaml ~/.claude/skills/agent-council/      # my member setup (edit the model name)
```

Requirements: Node.js, plus each member CLI installed and logged in (`codex`, `claude`, optionally `gemini`).

## Use

In Claude Code: "summon the council: <question>". Or directly:

```bash
~/.claude/skills/agent-council/scripts/council.sh "Should the dispatcher learn per-lane push rates or one shared uplink model?"
```

## How it fits the fleet

The council is the **thinking** half of the harness, the fleet is the **doing** half, and both are driven from the same
Claude Code session. A typical loop:

1. A hard design question comes up mid-task (a scheduling policy, a failed experiment, a stats protocol).
2. Claude summons the council; Codex and Claude answer independently, without seeing each other.
3. Claude chairs: merges the agreements, flags the disagreements, picks a plan.
4. The plan runs on the GPUs through `rig` / `rig dispatch`; results come back with `rig collect`.
5. `rig learn` + LESSONS.md record what actually happened, and the next council question starts from evidence.

Do not send secrets or unpublished data to the council: every member is a third-party service.
