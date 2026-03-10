# HTTP Examples

This folder contains sample YAML configurations for scanning external HTTP endpoints with `promptmap2.py`.

## Files
- `http-config-json.yaml`: JSON body example for a chat-style API.
- `http-config-form.yaml`: form-encoded request example.
- `http-config-openai-gpt5.yaml`: OpenAI-compatible chat completion example.
- `test.yaml`: local test configuration.

## How to use
Run:

```bash
python promptmap2.py --target-model external --target-model-type http --http-config http-examples/http-config-json.yaml --controller-model <controller_model> --controller-model-type <controller_type>
```

## Notes
- Include `{PAYLOAD_POSITION}` where attack prompts should be injected.
- Use `answer_focus_hint` to guide controller evaluation in noisy HTTP responses.
- Avoid committing real API keys in config files.
