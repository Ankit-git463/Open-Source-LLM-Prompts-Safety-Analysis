"""
app.py — Flask frontend for PromptMap.
"""
import os, sys, json, uuid, threading, datetime, traceback, logging
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, abort

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import (
    generate_responses, judge_saved_run, get_ollama_models, is_ollama_running,
    save_results_csv, save_results_json
)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("promptmap")

JOBS: dict = {}
JOBS_LOCK = threading.Lock()
RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)
RUNS_INDEX_FILE = RESULTS_DIR / "runs_index.json"

def create_job(job_id: str, cfg: dict, run_id: str, mode: str):
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "logs": [],
            "result": None,
            "config": cfg,
            "run_id": run_id,
            "mode": mode,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }


def append_job_log(job_id: str, msg: str):
    line = str(msg)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["logs"].append(line)
            job["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            mode = job.get("mode", "job")
            run_id = job.get("run_id", job_id)
        else:
            mode = "job"
            run_id = job_id
    logger.info("[%s:%s/%s] %s", mode, job_id, run_id, line)


def set_job_status(job_id: str, status: str, result: dict = None):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = status
            job["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            if result is not None:
                job["result"] = result
    logger.info("[job:%s] status=%s", job_id, status)


# ─── Run index ────────────────────────────────────────────────────────────────

def load_runs_index() -> list:
    if RUNS_INDEX_FILE.exists():
        with open(RUNS_INDEX_FILE, encoding="utf-8") as f:
            runs = json.load(f)
            for run in runs:
                if "status" not in run:
                    run["status"] = "judged"
                run.setdefault("judged_tests", run.get("total", 0) if run.get("status") == "judged" else 0)
                run.setdefault("pending_tests", 0 if run.get("status") == "judged" else run.get("total", 0))
            return runs
    return []

def save_runs_index(runs: list):
    with open(RUNS_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2)

def add_run(meta: dict):
    runs = [r for r in load_runs_index() if r["job_id"] != meta["job_id"]]
    runs.append(meta)
    save_runs_index(runs)

def load_run(job_id: str) -> dict:
    p = RESULTS_DIR / f"{job_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_run_meta(job_id: str, data: dict, cfg: dict = None) -> dict:
    meta = data.get("meta", {})
    return {
        "job_id": job_id,
        "model": meta.get("target_model", ""),
        "model_type": meta.get("target_model_type", ""),
        "timestamp": meta.get("started_at", ""),
        "finished_at": meta.get("finished_at", ""),
        "status": meta.get("status", "responses_saved"),
        "total": meta.get("total_tests", 0),
        "judged_tests": meta.get("judged_tests", 0),
        "pending_tests": meta.get("pending_tests", 0),
        "passed": meta.get("total_passed", 0),
        "failed": meta.get("total_failed", 0),
        "pass_rate": meta.get("pass_rate_pct", 0),
        "type_stats": meta.get("type_stats", {}),
        "sev_stats": meta.get("sev_stats", {}),
        "judge_models": meta.get("judge_models", []),
        "config": cfg or {},
    }


# ─── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/compare")
def compare_page():
    return render_template("compare.html")

@app.route("/run/<job_id>")
def run_detail(job_id):
    return render_template("run_detail.html", job_id=job_id)

@app.route("/history")
def history_page():
    return render_template("history.html")


# ─── Run API ──────────────────────────────────────────────────────────────────

@app.route("/api/ollama-models")
def api_ollama_models():
    url = request.args.get("url", "http://localhost:11434")
    return jsonify({"models": get_ollama_models(url), "running": is_ollama_running(url)})


@app.route("/api/run", methods=["POST"])
def api_run():
    cfg = request.json or {}
    judges = cfg.get("judges") or []
    run_id = str(uuid.uuid4())[:8]
    create_job(run_id, cfg, run_id, "generate")
    append_job_log(run_id, f"Queued target response generation for run {run_id}.")
    append_job_log(run_id, f"Target model: {cfg.get('target_model')} ({cfg.get('target_model_type')})")
    if judges:
        append_job_log(run_id, "Auto-judge enabled with " + ", ".join(f"{j.get('model')} ({j.get('model_type')})" for j in judges))
    else:
        append_job_log(run_id, "No judge models selected. This run will save target responses only.")

    def worker():
        def log(msg): append_job_log(run_id, msg)

        try:
            data = generate_responses(
                target_model=cfg["target_model"],
                target_model_type=cfg["target_model_type"],
                system_prompts_path=cfg.get("prompts_path", "system-prompts.txt"),
                iterations=int(cfg.get("iterations", 3)),
                severities=cfg.get("severities") or None,
                rule_names=cfg.get("rule_names") or None,
                rule_types=cfg.get("rule_types") or None,
                ollama_url=cfg.get("ollama_url", "http://localhost:11434"),
                google_api_key=cfg.get("google_api_key") or None,
                output_path=None,
                rules_dir=cfg.get("rules_dir", "rules"),
                log_fn=log,
            )

            # Persist files
            append_job_log(run_id, "Persisting target responses...")
            save_results_json(str(RESULTS_DIR / f"{run_id}.json"), data)
            save_results_csv(str(RESULTS_DIR / f"{run_id}.csv"), data)
            add_run(build_run_meta(run_id, data, cfg))

            if judges:
                append_job_log(run_id, "Target responses saved. Starting judge phase...")
                data = judge_saved_run(
                    data,
                    judges=judges,
                    ollama_url=cfg.get("ollama_url", "http://localhost:11434"),
                    google_api_key=cfg.get("google_api_key") or None,
                    log_fn=log,
                )
                append_job_log(run_id, "Persisting judged JSON and CSV outputs...")
                save_results_json(str(RESULTS_DIR / f"{run_id}.json"), data)
                save_results_csv(str(RESULTS_DIR / f"{run_id}.csv"), data)
                judged_cfg = dict(cfg)
                judged_cfg["judges"] = judges
                add_run(build_run_meta(run_id, data, judged_cfg))
                append_job_log(run_id, f"Run {run_id} complete. Responses and judgments saved.")
            else:
                append_job_log(run_id, f"Run {run_id} complete. Responses saved and ready for later judging.")

            set_job_status(run_id, "done", data)
        except Exception as e:
            append_job_log(run_id, f"Fatal error: {str(e)}")
            append_job_log(run_id, traceback.format_exc())
            set_job_status(run_id, "error")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": run_id, "run_id": run_id})


@app.route("/api/judge", methods=["POST"])
def api_judge():
    cfg = request.json or {}
    run_id = cfg.get("run_id", "").strip()
    judges = cfg.get("judges") or []
    if not run_id:
        return jsonify({"error": "run_id is required"}), 400
    if not judges:
        return jsonify({"error": "At least one judge model is required"}), 400

    task_id = str(uuid.uuid4())[:8]
    create_job(task_id, cfg, run_id, "judge")
    append_job_log(task_id, f"Queued judging for saved run {run_id}.")
    append_job_log(task_id, "Judge models: " + ", ".join(f"{j.get('model')} ({j.get('model_type')})" for j in judges))

    def worker():
        def log(msg): append_job_log(task_id, msg)

        try:
            data = load_run(run_id)
            if not data:
                raise FileNotFoundError(f"Run not found: {run_id}")

            updated = judge_saved_run(
                data,
                judges=judges,
                ollama_url=cfg.get("ollama_url", "http://localhost:11434"),
                google_api_key=cfg.get("google_api_key") or None,
                log_fn=log,
            )

            append_job_log(task_id, "Persisting judged JSON and CSV outputs...")
            save_results_json(str(RESULTS_DIR / f"{run_id}.json"), updated)
            save_results_csv(str(RESULTS_DIR / f"{run_id}.csv"), updated)

            existing_index = {r["job_id"]: r for r in load_runs_index()}
            merged_cfg = dict(existing_index.get(run_id, {}).get("config", {}))
            merged_cfg["judges"] = judges
            add_run(build_run_meta(run_id, updated, merged_cfg))

            append_job_log(task_id, f"Judging complete for run {run_id}.")
            set_job_status(task_id, "done", updated)
        except Exception as e:
            append_job_log(task_id, f"Fatal error: {str(e)}")
            append_job_log(task_id, traceback.format_exc())
            set_job_status(task_id, "error")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": task_id, "run_id": run_id})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
        if job:
            job["logs"] = list(job.get("logs", []))
    if not job:
        # Try loading from disk if server restarted
        data = load_run(job_id)
        if data:
            return jsonify({"status": "done", "logs": [], "result": data})
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status": job["status"],
        "logs": job["logs"],
        "result": job.get("result"),
        "run_id": job.get("run_id"),
        "mode": job.get("mode"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    })


@app.route("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        jobs = [
            {
                "job_id": job_id,
                "status": job.get("status"),
                "run_id": job.get("run_id"),
                "mode": job.get("mode"),
                "created_at": job.get("created_at"),
                "updated_at": job.get("updated_at"),
                "log_count": len(job.get("logs", [])),
                "config": job.get("config", {}),
            }
            for job_id, job in JOBS.items()
        ]
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jsonify({"jobs": jobs})


@app.route("/api/run-detail/<job_id>")
def api_run_detail(job_id):
    """Return the full saved JSON for a completed run."""
    data = load_run(job_id)
    if not data:
        abort(404)
    run_index = next((r for r in load_runs_index() if r["job_id"] == job_id), None)
    if run_index:
        data["config"] = run_index.get("config", {})
    return jsonify(data)


@app.route("/api/download/<job_id>/<fmt>")
def api_download(job_id, fmt):
    if fmt not in ("json", "csv"):
        abort(400)
    path = RESULTS_DIR / f"{job_id}.{fmt}"
    if not path.exists():
        abort(404)
    mime = "application/json" if fmt == "json" else "text/csv"
    return send_file(str(path), mimetype=mime, as_attachment=True,
                     download_name=f"results_{job_id}.{fmt}")


# ─── Comparison API ───────────────────────────────────────────────────────────

@app.route("/api/runs")
def api_runs():
    return jsonify(load_runs_index())

@app.route("/api/runs/delete/<job_id>", methods=["DELETE"])
def api_delete_run(job_id):
    save_runs_index([r for r in load_runs_index() if r["job_id"] != job_id])
    for fmt in ("json", "csv"):
        p = RESULTS_DIR / f"{job_id}.{fmt}"
        if p.exists(): p.unlink()
    return jsonify({"ok": True})

@app.route("/api/compare")
def api_compare():
    ids = [i.strip() for i in request.args.get("ids","").split(",") if i.strip()]
    if not ids:
        return jsonify({"error": "no ids"}), 400

    index = {r["job_id"]: r for r in load_runs_index()}
    raw_runs = []
    
    for job_id in ids:
        meta = index.get(job_id)
        if not meta or meta.get("status") != "judged":
            continue
        raw = load_run(job_id)
        tests = raw.get("tests") if "tests" in raw else raw
        per_test = {name: t.get("passed", False) for name, t in tests.items() if isinstance(t, dict) and name != "meta"}
        raw_runs.append((job_id, meta, per_test))

    all_tests = sorted({t for _, _, per_test in raw_runs for t in per_test})
    total_union = len(all_tests)
    
    comparison = []
    for job_id, meta, per_test in raw_runs:
        passed = sum(1 for t in all_tests if per_test.get(t) is True)
        failed = total_union - passed
        pass_rate = round((passed / total_union) * 100, 1) if total_union > 0 else 0
        comparison.append({
            "job_id":     job_id,
            "model":      meta["model"],
            "model_type": meta["model_type"],
            "timestamp":  meta["timestamp"],
            "total":      total_union,
            "passed":     passed,
            "failed":     failed,
            "pass_rate":  pass_rate,
            "type_stats": meta.get("type_stats", {}),
            "sev_stats":  meta.get("sev_stats", {}),
            "per_test":   per_test,
        })

    return jsonify({"runs": comparison, "all_tests": all_tests})


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5000, use_reloader=False)
