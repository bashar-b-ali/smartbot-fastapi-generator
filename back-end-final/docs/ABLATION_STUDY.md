# Ablation Study

This project includes a deterministic local ablation harness for the model-owned FastAPI generation pipeline.

Run:

```bash
python scripts/ablation_study.py
python scripts/ablation_study.py --markdown
```

The harness does not call live models or the internet. It uses scripted provider responses to compare pipeline behavior across representative strategies.

Tracked metrics:

- accepted/rejected result
- generation strategy
- provider call count
- input/output token counters
- retry count
- runtime validation status
- elapsed time
- missing artifacts and primary failure category

Current fixture families:

- `renderer_fast_path`: canonical SQLModel resource API covered by deterministic rendering.
- `model_per_file`: model-planned file generation using scripted file responses.

Use this report before final edits to compare whether a pipeline change improves acceptance, runtime, and token use without broadening generated behavior.

