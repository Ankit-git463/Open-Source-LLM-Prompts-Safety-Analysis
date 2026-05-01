# PromptMap Project Documentation

## 1. Project Overview

PromptMap is a security evaluation platform for testing system prompts used in LLM-based applications. It helps developers, researchers, and evaluators check whether a model follows its intended role and safety boundaries when exposed to adversarial user prompts.

The system is implemented as a Flask web application backed by a Python testing engine. The user can select a target model, run adversarial prompt rules, save the generated responses, judge those responses with one or more evaluation models, and compare results across runs.

## 2. Problem Statement

LLM applications commonly rely on system prompts to define:

- the assistant's identity and role
- allowed and disallowed behavior
- safety instructions
- task boundaries
- private or internal instructions

However, user messages can be crafted to override, bypass, distract, or extract these instructions. Attacks such as jailbreaks, prompt injection, prompt stealing, judge injection, and harmful instruction requests can cause an LLM to violate the intended behavior of the application.

The key problem is that prompt security is difficult to test manually. Manual evaluation is slow, inconsistent, hard to repeat, and often misses edge cases. PromptMap addresses this by creating a repeatable framework for running a collection of adversarial tests against a system prompt and recording whether the model resisted or failed each attack.

## 3. Objectives

- Provide a practical tool for LLM prompt security testing.
- Automate adversarial testing with reusable YAML rules.
- Support different model providers for target and judge models.
- Preserve raw target responses before judging so evaluation can be repeated.
- Use multiple judges to reduce dependence on a single evaluator model.
- Store detailed evidence for every test iteration.
- Provide summaries and comparisons for model performance analysis.

## 4. Features

### 4.1 Web Interface

The Flask UI provides pages for:

- configuring and running tests
- viewing saved run history
- inspecting full details for a run
- comparing multiple judged runs

### 4.2 Model Provider Support

PromptMap supports:

- Ollama for local model testing
- Google Gemini through the `google-genai` package

The target model and judge models can use either supported provider, depending on the configuration passed through the UI.

### 4.3 Two-Stage Workflow

PromptMap separates execution into two stages:

1. Response generation: attack prompts are sent to the target model and responses are stored.
2. Judging: stored responses are evaluated by one or more judge models.

This separation makes it possible to re-judge saved model outputs without rerunning the target model.

### 4.4 YAML Rule Library

Rules are stored under the `rules/` directory. Each rule defines:

- name
- type/category
- severity
- attack prompt
- pass conditions
- fail conditions

This makes the test suite extensible without changing Python code.

### 4.5 Attack Categories

The repository includes rules for:

- Distraction: attempts to pull the assistant away from its intended task.
- Harmful requests: requests for unsafe, illegal, or abusive assistance.
- Hate: attempts to produce hateful or discriminatory output.
- Jailbreaks: attempts to override policies or role constraints.
- Prompt stealing: attempts to reveal the system prompt.
- Social bias: attempts to elicit biased or stereotyped behavior.
- Judge injection: attempts to manipulate the evaluator or scoring logic.

### 4.6 Deterministic Checks

The engine includes programmatic checks for:

- refusal signals
- n-gram system prompt leakage
- sentence-level prompt overlap
- judge manipulation patterns
- actionable formatting in unsafe contexts

These checks complement judge-model evaluation and provide additional evidence.

### 4.7 Multi-Judge Voting

During judging, each configured judge model evaluates each saved target response. Votes are aggregated into a final verdict.

For high-risk categories such as harmful, jailbreak, prompt stealing, and judge injection, PromptMap uses a conservative policy: if any judge marks a response as failed, the final result fails.

For other categories, the system uses majority voting, with ties treated as failures.

### 4.8 Result Storage And Export

PromptMap stores:

- full JSON results in `results/<run_id>.json`
- CSV exports in `results/<run_id>.csv`
- run metadata in `results/runs_index.json`

The UI can download JSON/CSV files and compare saved judged runs.

## 5. System Architecture

```text
User Browser
    |
    v
Flask Web Application - app.py
    |
    v
PromptMap Engine - engine.py
    |
    +--------------------+
    |                    |
    v                    v
Target LLM          Judge LLM(s)
Ollama/Gemini       Ollama/Gemini
    |
    v
Results, Rules, System Prompts
```

### 5.1 Presentation Layer

The presentation layer is built with Flask templates:

- `templates/index.html`: main test configuration and execution page
- `templates/history.html`: saved run history
- `templates/run_detail.html`: full report for one run
- `templates/compare.html`: comparison view across runs

### 5.2 API Layer

`app.py` exposes API endpoints for running tests, judging saved runs, polling jobs, downloading outputs, deleting runs, and comparing runs.

Background work is handled with Python threads. Each running job stores its status, logs, result data, configuration, and mode in the in-memory `JOBS` dictionary.

### 5.3 Engine Layer

`engine.py` contains the core project logic:

- provider initialization
- LLM API calls
- rule loading
- system prompt loading
- response generation
- deterministic analysis
- judge prompt construction
- LLM-based evaluation
- vote aggregation
- summary recomputation
- JSON/CSV persistence

### 5.4 Data Layer

PromptMap uses file-based storage:

- `rules/` for YAML test rules
- `SystemPrompts/` for example system prompts
- `results/` for generated run files
- `templates/` for UI pages

No database is required.

## 6. Module Responsibilities

### 6.1 `app.py`

Responsibilities:

- create the Flask app
- serve HTML pages
- expose API endpoints
- manage background jobs
- save generated and judged results
- maintain `runs_index.json`
- provide download and comparison APIs

Important functions:

- `load_runs_index()`
- `save_runs_index()`
- `build_run_meta()`
- `api_run()`
- `api_judge()`
- `api_job()`
- `api_run_detail()`
- `api_compare()`

### 6.2 `engine.py`

Responsibilities:

- connect to Ollama or Google Gemini
- load YAML rules and system prompts
- call target and judge models
- detect prompt leakage
- evaluate outputs
- aggregate judge votes
- compute summary statistics
- export results

Important functions:

- `initialize_client()`
- `load_test_rules()`
- `load_system_prompt()`
- `call_model()`
- `generate_responses()`
- `judge_saved_run()`
- `evaluate_result()`
- `aggregate_judge_votes()`
- `recompute_run_summary()`
- `save_results_json()`
- `save_results_csv()`

### 6.3 `Prompts.py`

Responsibilities:

- define the default controller system prompt
- define type-specific judge prompts for jailbreak, harmful, hate, distraction, and social bias tests

The judge prompts instruct the evaluator to treat target model output as untrusted evidence and to return only `pass` or `fail`.

## 7. Detailed Execution Flow

### 7.1 Run Generation Flow

```text
User submits run config
    |
POST /api/run
    |
Create background job and run_id
    |
engine.generate_responses()
    |
Initialize target client
    |
Load selected system prompt
    |
Load YAML rules
    |
Apply severity/type/name filters
    |
For each rule:
    |
Send attack prompt to target model
    |
Save target response and deterministic findings
    |
Save results JSON and CSV
```

### 7.2 Judging Flow

```text
User selects saved run and judge models
    |
POST /api/judge
    |
Load results/<run_id>.json
    |
engine.judge_saved_run()
    |
Initialize judge clients
    |
For each test iteration:
    |
Run deterministic checks
    |
Send response to judge model(s)
    |
Collect judge votes
    |
Aggregate votes into final verdict
    |
Recompute run summary
    |
Update JSON, CSV, and run index
```

### 7.3 Comparison Flow

```text
User selects judged runs
    |
GET /api/compare?ids=...
    |
Load run metadata from runs_index.json
    |
Load each selected result JSON
    |
Build union of tested rule names
    |
Compute pass/fail/pass-rate per run
    |
Return comparison payload to UI
```

## 8. Data Model

### 8.1 Run JSON

A saved result file has two top-level keys:

```json
{
  "meta": {},
  "tests": {}
}
```

`meta` stores configuration and summary information:

- target model
- target model type
- judge models
- system prompt
- filters
- timestamps
- workflow status
- total tests
- judged tests
- pending tests
- pass/fail totals
- pass rate
- type statistics
- severity statistics

`tests` stores each rule result keyed by rule name.

### 8.2 Test Result

Each test result contains:

- original rule fields
- `passed`
- `pass_rate`
- `passed_count`
- `total_iterations`
- `iterations`
- `judged`

### 8.3 Iteration Record

Each iteration record contains:

- iteration number
- timestamp
- target system prompt
- attack prompt
- target LLM response
- error flag
- deterministic findings
- judge votes
- controller input/output
- final verdict
- reason
- vote policy
- judge agreement metrics

## 9. Rule Design

Rules are simple YAML files:

```yaml
name: example_rule
type: harmful
severity: high
prompt: "Adversarial user request goes here."
pass_conditions:
  - "The model refuses the unsafe request."
fail_conditions:
  - "The model provides actionable unsafe instructions."
```

Good rules should:

- have one clear objective
- include concrete pass and fail conditions
- use accurate severity labels
- avoid ambiguous expected behavior
- keep the attack prompt realistic

## 10. Evaluation Logic

### 10.1 Prompt Stealing

Prompt stealing receives special handling. The engine checks whether the target response leaks the system prompt through:

- exact n-gram overlap
- sentence-level overlap
- semantic leakage judged by an LLM

### 10.2 Standard Rule Evaluation

For most rule types, the engine sends the following to the judge:

- pass conditions
- fail conditions
- target model output
- type-specific controller prompt

The judge is instructed to return only `pass` or `fail`.

### 10.3 Vote Aggregation

The aggregation policy is:

- no judges means failure
- high-risk category with any fail vote means failure
- otherwise majority pass means pass
- tie or majority fail means failure

This makes the framework stricter for security-sensitive categories.

## 11. Setup And Running

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the Flask app:

```bash
python app.py
```

Open:

```text
http://localhost:5000
```

For Google Gemini:

```bash
set GOOGLE_API_KEY=your_api_key_here
```

For Ollama, ensure the local server is available at:

```text
http://localhost:11434
```

## 12. API Reference

| Endpoint | Method | Description |
|---|---:|---|
| `/` | GET | Main test runner UI |
| `/history` | GET | Saved run history |
| `/compare` | GET | Run comparison page |
| `/run/<job_id>` | GET | Run detail page |
| `/api/ollama-models` | GET | Get installed Ollama models |
| `/api/run` | POST | Start response generation |
| `/api/judge` | POST | Start judging a saved run |
| `/api/job/<job_id>` | GET | Poll a background job |
| `/api/run-detail/<job_id>` | GET | Get full saved run data |
| `/api/download/<job_id>/<fmt>` | GET | Download JSON or CSV |
| `/api/runs` | GET | List saved runs |
| `/api/runs/delete/<job_id>` | DELETE | Delete a run |
| `/api/compare` | GET | Compare selected judged runs |

## 13. Technologies Used

- Python
- Flask
- Bootstrap
- Bootstrap Icons
- PyYAML
- Requests
- Ollama Python client
- Google GenAI SDK
- tiktoken
- JSON and CSV file storage

## 14. Strengths

- Simple file-based architecture.
- Easy to extend with new YAML rules.
- Supports both local and hosted models.
- Stores complete evidence for every result.
- Separates response generation from judging.
- Enables multi-judge evaluation.
- Includes deterministic checks in addition to LLM judging.

## 15. Limitations

- Results are stored in local files, not a database.
- Background jobs are in memory, so active job state is lost if the server restarts.
- LLM-as-judge evaluation can still be imperfect.
- The UI assumes the Flask process has file access to prompt, rule, and result directories.
- Provider support is currently limited to Ollama and Google Gemini.

## 16. Future Scope

- Add authentication for multi-user deployments.
- Add database storage for runs and job state.
- Add OpenAI and Anthropic provider support in the UI workflow.
- Add scheduled regression testing for prompt updates.
- Add richer visual analytics for category and severity trends.
- Add rule authoring and validation inside the UI.
- Add exportable PDF reports.
- Add CI integration for automated prompt safety checks.

## 17. Conclusion

PromptMap provides a structured and repeatable way to evaluate LLM prompt security. By combining adversarial YAML rules, target model execution, deterministic checks, multi-judge evaluation, and saved result comparison, it helps users understand where their system prompts are strong and where they may fail under attack.
