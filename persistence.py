"""SQLite persistence for PromptMap runs.

JSON and CSV files remain export formats, while SQLite is the source of truth
for run metadata and normalized response/evaluation records.
"""
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    target_model TEXT,
    target_model_type TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    UNIQUE(name, model_type)
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT,
    severity TEXT,
    rule_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    iteration_number INTEGER NOT NULL,
    target_model_id INTEGER REFERENCES models(id),
    timestamp TEXT,
    attack_prompt TEXT,
    system_prompt TEXT,
    response_text TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id, rule_id, iteration_number)
);

CREATE TABLE IF NOT EXISTS deterministic_findings (
    id INTEGER PRIMARY KEY,
    response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    finding_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS judge_evaluations (
    id INTEGER PRIMARY KEY,
    response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    judge_model_id INTEGER REFERENCES models(id),
    verdict TEXT NOT NULL CHECK(verdict IN ('pass', 'fail', 'error')),
    reason TEXT,
    evaluation_json TEXT NOT NULL,
    UNIQUE(response_id, judge_model_id)
);

CREATE TABLE IF NOT EXISTS aggregate_results (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    passed INTEGER,
    pass_rate TEXT,
    result_json TEXT NOT NULL,
    UNIQUE(run_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_responses_run ON responses(run_id);
CREATE INDEX IF NOT EXISTS idx_responses_rule ON responses(rule_id);
CREATE INDEX IF NOT EXISTS idx_judge_evaluations_response ON judge_evaluations(response_id);
CREATE INDEX IF NOT EXISTS idx_aggregate_results_run ON aggregate_results(run_id);
"""


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, factory=_Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)


def _model_id(connection: sqlite3.Connection, model: str, model_type: str) -> Optional[int]:
    if not model or not model_type:
        return None
    connection.execute(
        "INSERT OR IGNORE INTO models(name, model_type) VALUES (?, ?)",
        (model, model_type),
    )
    row = connection.execute(
        "SELECT id FROM models WHERE name = ? AND model_type = ?",
        (model, model_type),
    ).fetchone()
    return row["id"] if row else None


def save_run(path: str, run_id: str, data: Dict, config: Optional[Dict] = None) -> None:
    """Store a complete run document and its normalized records atomically."""
    meta = data.get("meta", {})
    config = config or {}
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO runs(
                run_id, status, started_at, finished_at, target_model,
                target_model_type, config_json, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                target_model=excluded.target_model,
                target_model_type=excluded.target_model_type,
                config_json=excluded.config_json,
                data_json=excluded.data_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                run_id,
                meta.get("status", "responses_saved"),
                meta.get("started_at"),
                meta.get("finished_at"),
                meta.get("target_model"),
                meta.get("target_model_type"),
                json.dumps(config, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False),
            ),
        )
        connection.execute("DELETE FROM responses WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM aggregate_results WHERE run_id = ?", (run_id,))

        target_model_id = _model_id(
            connection, meta.get("target_model", ""), meta.get("target_model_type", "")
        )
        for name, rule in data.get("tests", {}).items():
            connection.execute(
                """
                INSERT INTO rules(name, type, severity, rule_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    type=excluded.type,
                    severity=excluded.severity,
                    rule_json=excluded.rule_json
                """,
                (
                    name,
                    rule.get("type"),
                    rule.get("severity"),
                    json.dumps(rule, ensure_ascii=False),
                ),
            )
            rule_row = connection.execute(
                "SELECT id FROM rules WHERE name = ?", (name,)
            ).fetchone()
            rule_id = rule_row["id"]
            connection.execute(
                """
                INSERT INTO aggregate_results(run_id, rule_id, passed, pass_rate, result_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rule_id,
                    None if rule.get("passed") is None else int(bool(rule["passed"])),
                    rule.get("pass_rate"),
                    json.dumps(rule, ensure_ascii=False),
                ),
            )
            for iteration in rule.get("iterations", []):
                cursor = connection.execute(
                    """
                    INSERT INTO responses(
                        run_id, rule_id, iteration_number, target_model_id,
                        timestamp, attack_prompt, system_prompt, response_text, is_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        rule_id,
                        iteration.get("iteration", 0),
                        target_model_id,
                        iteration.get("timestamp"),
                        iteration.get("attack_prompt"),
                        iteration.get("target_system_prompt"),
                        iteration.get("llm_response"),
                        int(bool(iteration.get("is_error"))),
                    ),
                )
                response_id = cursor.lastrowid
                for finding in iteration.get("deterministic_findings", []):
                    connection.execute(
                        "INSERT INTO deterministic_findings(response_id, finding_json) VALUES (?, ?)",
                        (response_id, json.dumps(finding, ensure_ascii=False)),
                    )
                for vote in iteration.get("judge_votes", []):
                    judge_id = _model_id(
                        connection, vote.get("model", ""), vote.get("model_type", "")
                    )
                    verdict = vote.get("verdict", "error")
                    if verdict not in {"pass", "fail", "error"}:
                        verdict = "error"
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO judge_evaluations(
                            response_id, judge_model_id, verdict, reason, evaluation_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            response_id,
                            judge_id,
                            verdict,
                            vote.get("reason"),
                            json.dumps(vote, ensure_ascii=False),
                        ),
                    )


def load_run(path: str, run_id: str) -> Dict:
    with connect(path) as connection:
        row = connection.execute(
            "SELECT data_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return json.loads(row["data_json"]) if row else {}


def list_runs(path: str) -> List[Dict]:
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT run_id, config_json, data_json FROM runs ORDER BY created_at"
        ).fetchall()
    result = []
    for row in rows:
        data = json.loads(row["data_json"])
        meta = data.get("meta", {})
        result.append({
            "job_id": row["run_id"],
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
            "total_planned_tests": meta.get("total_planned_tests", meta.get("total_tests", 0)),
            "config": json.loads(row["config_json"]),
        })
    return result


def delete_run(path: str, run_id: str) -> None:
    with connect(path) as connection:
        connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
