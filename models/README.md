# Local model weights (not committed)

Place unpacked Hugging Face-style checkpoints anywhere on your machine, then map each logical `model_id` to an absolute path in [`models/registry.json`](./registry.json).

Example registry:

```json
{
  "Qwen/Qwen2.5-0.5B-Instruct": {
    "path": "/absolute/path/to/Qwen__Qwen2.5-0.5B-Instruct"
  }
}
```

Each `path` must be absolute and point to a directory containing safetensors weights.

Generate planner byte profiles by calling the model info interface:

```python
from models import ensure_model_profile

ensure_model_profile("Qwen/Qwen2.5-0.5B-Instruct")
```

Inspect weights directly from an explicit local path:

```python
from models.model_info import analyze_model_dir

payload = analyze_model_dir("/path/to/model")
print(payload["layer_bytes"])
```
