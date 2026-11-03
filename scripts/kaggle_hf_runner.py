"""Run the PromptMap rules against Hugging Face models sequentially.

This script is designed for Kaggle notebooks. It writes one frontend-compatible
JSON and CSV file per model under ``results/`` and updates ``runs_index.json``.
Start ``app.py`` in the same directory afterward to view the saved runs.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from huggingface_hub import get_token

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    deterministic_response_checks,
    load_system_prompt,
    load_test_rules,
    should_fail_from_deterministic,
)
try:
    from persistence import initialize, save_run as save_persisted_run  # noqa: E402
except ImportError:
    initialize = None
    save_persisted_run = None


DEFAULT_MODELS = {
    "openchat": "openchat/openchat-3.5-0106",
    "mistral": "mistralai/Ministral-3-8B-Instruct-2512",
    "qwen2.5": "Qwen/Qwen2.5-7B-Instruct",
    "phi4-mini": "microsoft/Phi-4-mini-instruct",
    "llama3.2": "meta-llama/Llama-3.2-3B-Instruct",
    "qwen3.5": "Qwen/Qwen3.5-4B",
    "gemma3": "google/gemma-3-4b-it",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=list(DEFAULT_MODELS), help="Model keys to run")
    parser.add_argument("--system-prompt", default="SystemPrompts/technical-support.txt")
    parser.add_argument("--rules-dir", default="rules")
    parser.add_argument("--rule-type", action="append", dest="rule_types")
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--model-id", action="append", default=[], metavar="KEY=REPO")
    return parser.parse_args()


def model_map(overrides: list[str]) -> dict[str, str]:
    result = dict(DEFAULT_MODELS)
    for item in overrides:
        key, separator, repo = item.partition("=")
        if not separator or not key or not repo:
            raise ValueError(f"Invalid --model-id value: {item}; expected KEY=REPOSITORY")
        result[key] = repo
    return result


def load_model(repo: str, load_in_4bit: bool):
    token = os.environ.get("HF_TOKEN") or get_token()
    auth = {"token": token} if token else {}
    tokenizer = AutoTokenizer.from_pretrained(repo, use_fast=True, **auth)
    kwargs = {"device_map": "auto"}
    if torch.cuda.is_available():
        kwargs["torch_dtype"] = torch.float16
    if load_in_4bit:
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(repo, **kwargs, **auth)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer, model


def clear_model_cache() -> None:
    """Release Python and CUDA allocations before the next model is loaded."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def generate(tokenizer, model, system_prompt: str, attack_prompt: str, max_new_tokens: int) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": attack_prompt},
    ]
    if tokenizer.chat_template:
        encoded = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        )
    else:
        text = f"System: {system_prompt}\n\nUser: {attack_prompt}\n\nAssistant:"
        encoded = tokenizer(text, return_tensors="pt").input_ids
    device = next(model.parameters()).device
    encoded = encoded.to(device)
    with torch.inference_mode():
        output = model.generate(
            encoded,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
    new_tokens = output[0, encoded.shape[-1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def evaluate_model(
    model_key: str,
    repo: str,
    system_prompt: str,
    rules: dict,
    iterations: int,
    max_new_tokens: int,
    load_in_4bit: bool,
) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    tokenizer, model = load_model(repo, load_in_4bit)
    tests = {}
    generation_errors = 0
    for name, source_rule in rules.items():
        rule = {**source_rule, "name": name}
        records = []
        for iteration in range(1, iterations + 1):
            began = time.perf_counter()
            error = None
            response = ""
            try:
                response = generate(
                    tokenizer, model, system_prompt, rule["prompt"], max_new_tokens
                )
            except Exception as exc:
                error = str(exc)
                generation_errors += 1
                raise RuntimeError(
                    f"Generation failed for test '{name}' on iteration "
                    f"{iteration}: {error}"
                ) from exc
            findings = (
                deterministic_response_checks(rule, response, system_prompt)
                if error is None
                else [{"kind": "generation_error", "message": error}]
            )
            failed = error is not None or should_fail_from_deterministic(rule, findings)
            records.append({
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "target_system_prompt": system_prompt,
                "attack_prompt": rule["prompt"],
                "llm_response": response if error is None else f"Error: {error}",
                "is_error": error is not None,
                "target_latency_ms": round((time.perf_counter() - began) * 1000, 2),
                "deterministic_findings": findings,
                "verdict": "fail" if failed else "pass",
                "passed": not failed,
                "reason": (
                    "Deterministic safety checks passed."
                    if not failed
                    else f"{len(findings)} deterministic finding(s)."
                ),
            })
        passed = all(item["passed"] for item in records)
        tests[name] = {
            **rule,
            "passed": passed,
            "passed_count": sum(item["passed"] for item in records),
            "total_iterations": len(records),
            "pass_rate": f"{sum(item['passed'] for item in records)}/{len(records)}",
            "iterations": records,
        }
        completed = len(tests)
        if completed == 1 or completed % 10 == 0 or completed == len(rules):
            print(
                f"[{model_key}] completed {completed}/{len(rules)} tests "
                f"({completed / len(rules) * 100:.1f}%)",
                flush=True,
            )

    finished = datetime.now().isoformat(timespec="seconds")
    type_stats, sev_stats = {}, {}
    for test in tests.values():
        for bucket, key in ((type_stats, "type"), (sev_stats, "severity")):
            value = test[key]
            bucket.setdefault(value, {"passed": 0, "total": 0})
            bucket[value]["total"] += 1
            bucket[value]["passed"] += int(test["passed"])
            bucket[value]["pass_rate_pct"] = round(
                bucket[value]["passed"] / bucket[value]["total"] * 100, 1
            )
    passed_total = sum(int(test["passed"]) for test in tests.values())
    output = {
        "meta": {
            "target_model": model_key,
            "target_model_id": repo,
            "target_model_type": "huggingface",
            "evaluation_mode": "deterministic_only",
            "system_prompts_path": "",
            "system_prompt": system_prompt,
            "iterations_per_test": iterations,
            "rule_types_filter": None,
            "started_at": started,
            "finished_at": finished,
            "status": "judged",
            "judge_models": [],
            "latest_judgment": {"mode": "deterministic_only", "judge_count": 0},
            "total_tests": len(tests),
            "judged_tests": len(tests),
            "pending_tests": 0,
            "total_passed": passed_total,
            "total_failed": len(tests) - passed_total,
            "generation_errors": generation_errors,
            "pass_rate_pct": round(passed_total / len(tests) * 100, 1) if tests else 0,
            "type_stats": type_stats,
            "sev_stats": sev_stats,
        },
        "tests": tests,
    }
    del model, tokenizer
    clear_model_cache()
    return output


def save_run(output_dir: Path, run_id: str, data: dict, config: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    data["meta"]["system_prompts_path"] = config["system_prompt"]
    json_path = output_dir / f"{run_id}.json"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with (output_dir / f"{run_id}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["test", "model", "severity", "passed", "pass_rate", "response"])
        for name, test in data["tests"].items():
            response = test["iterations"][0]["llm_response"] if test["iterations"] else ""
            writer.writerow([
                name, data["meta"]["target_model"], test["severity"],
                test["passed"], test["pass_rate"], response,
            ])
    index_path = output_dir / "runs_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    index = [item for item in index if item.get("job_id") != run_id]
    meta = data["meta"]
    index.append({
        "job_id": run_id,
        "model": meta["target_model"],
        "model_type": meta["target_model_type"],
        "timestamp": meta["started_at"],
        "finished_at": meta["finished_at"],
        "status": meta["status"],
        "total": meta["total_tests"],
        "judged_tests": meta["judged_tests"],
        "pending_tests": 0,
        "passed": meta["total_passed"],
        "failed": meta["total_failed"],
        "pass_rate": meta["pass_rate_pct"],
        "type_stats": meta["type_stats"],
        "sev_stats": meta["sev_stats"],
        "judge_models": [],
        "config": config,
    })
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    if initialize is not None and save_persisted_run is not None:
        database_path = output_dir / "promptmap.sqlite3"
        initialize(str(database_path))
        save_persisted_run(str(database_path), run_id, data, config)


def main() -> None:
    args = parse_args()
    models = model_map(args.model_id)
    unknown = [key for key in args.models if key not in models]
    if unknown:
        raise SystemExit(f"Unknown model key(s): {', '.join(unknown)}")
    system_prompt = load_system_prompt(args.system_prompt)
    rules = load_test_rules(args.rules_dir, rule_types=args.rule_types)
    output_dir = Path(args.output_dir)
    for key in args.models:
        run_id = f"hf-{key}-{uuid.uuid4().hex[:8]}"
        print(f"\n[{key}] {models[key]} — {len(rules)} tests")
        try:
            data = evaluate_model(
                key, models[key], system_prompt, rules, args.iterations,
                args.max_new_tokens, args.load_in_4bit,
            )
            save_run(
                output_dir,
                run_id,
                data,
                vars(args) | {
                    "model_id": models[key],
                    "system_prompt": args.system_prompt,
                    "model_repo": models[key],
                },
            )
            print(f"Saved {run_id}: {data['meta']['pass_rate_pct']}% deterministic pass rate")
        except Exception as exc:
            message = str(exc)
            if "gated repo" in message.lower() or "access to model" in message.lower():
                print(
                    f"SKIPPED {key}: Hugging Face access is not authorized for "
                    f"{models[key]}. Accept the model license and grant access "
                    f"to the logged-in account, then rerun this model.",
                    file=sys.stderr,
                )
            else:
                print(f"FAILED {key}: {exc}", file=sys.stderr)
        finally:
            clear_model_cache()
            if torch.cuda.is_available():
                print(
                    f"GPU cache cleared. Allocated: "
                    f"{torch.cuda.memory_allocated() / 1024**2:.1f} MiB; "
                    f"reserved: {torch.cuda.memory_reserved() / 1024**2:.1f} MiB"
                )


if __name__ == "__main__":
    main()
