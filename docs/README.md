# PromptMap Documentation README

This folder contains the long-form technical documentation for PromptMap.

## What PromptMap Solves

PromptMap is a backend-first framework for evaluating LLM prompt security in open-source deployments. It automates adversarial prompt testing, deterministic safety checks, and multi-judge decision aggregation to measure how robust a system prompt is against prompt injection, jailbreak, prompt stealing, and judge manipulation.

## Core Documentation

- Main document: `docs/DOCUMENTATION.md`
- Architecture diagram: `docs/ARCHITECTURE_README.md`

## Results Snapshot (From `results/runs_index.json`)

| Run ID | Target Model | Attack Type | Total | Passed | Failed | Pass Rate |
|---|---|---|---:|---:|---:|---:|
| `68716ae9` | `qwen3:latest` | jailbreak | 200 | 191 | 9 | 95.5% |
| `e1f3433b` | `llama3.2:latest` | jailbreak | 200 | 182 | 18 | 91.0% |
| `cee28526` | `openchat:latest` | jailbreak | 177 | 118 | 59 | 66.7% |
| `5e7ef051` | `openchat:latest` | judge_injection | 54 | 22 | 32 | 40.7% |
| `d836bc09` | `openchat:latest` | prompt_stealing | 26 | 16 | 10 | 61.5% |

## Why This Matters

- Open-source LLMs are highly customizable, but security validation is usually ad hoc.
- PromptMap provides a repeatable workflow: generate responses first, then judge later.
- The framework preserves complete evidence so teams can re-evaluate with different judges and compare runs over time.
