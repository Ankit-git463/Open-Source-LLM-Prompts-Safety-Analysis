# PromptMap Architecture README

This document describes the backend architecture and execution pipeline used by PromptMap.

## System Architecture Diagram

```mermaid
flowchart TD
    U[User / Browser UI] --> A[Flask App - app.py]
    A --> B[Job Orchestration Layer\nJOBS + background threads + checkpoints]
    B --> C[PromptMap Engine - engine.py]

    C --> D[Load System Prompt\nSystemPrompts/]
    C --> E[Load YAML Rules\nrules/]
    C --> F[Target Model Inference\nOllama or Gemini]

    F --> G[Raw Response + Iteration Evidence]
    G --> H[Deterministic Checks\nrefusal, leak overlap, judge-injection, unsafe formatting]
    H --> I[Judge Evaluation\none or more judge models]
    I --> J[Vote Aggregation\nmajority or high-risk conservative policy]

    J --> K[Run Summary Metrics\npass/fail, severity stats, agreement]
    K --> L[Persistence Layer\nresults/*.json, results/*.csv, runs_index.json]

    L --> M[History / Compare / Download APIs]
```

## Pipeline Notes

1. Generation stage and judging stage are decoupled.
2. Saved runs can be re-judged without re-running the target model.
3. Multi-judge voting improves evaluation stability.
4. High-risk rule types are evaluated with stricter failure policy.

## Main Files

- `app.py`: API endpoints, async job execution, run lifecycle management.
- `engine.py`: model calls, deterministic checks, judge voting, summaries, persistence.
- `Prompts.py`: judge/controller prompts with pass/fail constrained outputs.
