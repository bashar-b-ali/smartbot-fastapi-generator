"""
Local Transformer Provider
Loads the fine-tuned FastAPIBot model (or any local HF model) directly
via transformers, without needing Ollama.

This allows local evaluation of the fine-tuned model alongside API-based models.
"""

import logging
import os
import time
from collections.abc import Generator

from .providers import LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)

# Lazy-loaded globals (model stays in memory across requests)
_local_model = None
_local_tokenizer = None
_local_model_path = None


class LocalTransformerProvider(LLMProvider):
    """
    Provider that loads a local HuggingFace model for inference.
    Supports both base models and LoRA-merged models.
    """

    DEFAULT_MODEL_PATHS = [
        "../bot-training/output/fastAPI_Model-merged",
        "../bot-training/output/fastAPI_Model-lora",
        "../bot-training/models/fastAPI_Model",
    ]

    def __init__(self, model: str | None = None, api_key: str | None = None, **kwargs):
        super().__init__(api_key=None, model=model or "fastapibot-local")
        self._model_path = model
        self._device = None

    def _find_model_path(self) -> str:
        """Find the best available local model."""
        if self._model_path and os.path.exists(self._model_path):
            return self._model_path

        # Search from project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        for candidate in self.DEFAULT_MODEL_PATHS:
            full_path = os.path.join(project_root, candidate)
            config_path = os.path.join(full_path, "config.json")
            if os.path.exists(config_path):
                logger.info(f"Found local model: {full_path}")
                return full_path

        raise FileNotFoundError(f"No local model found. Searched: {self.DEFAULT_MODEL_PATHS}.")

    def _load_model(self):
        """Load model into GPU memory (cached globally)."""
        global _local_model, _local_tokenizer, _local_model_path

        model_path = self._find_model_path()

        # Reuse if same model is already loaded
        if _local_model is not None and _local_model_path == model_path:
            return

        logger.info(f"Loading local model from {model_path}...")

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        _local_tokenizer = AutoTokenizer.from_pretrained(
            model_path, trust_remote_code=True
        )
        if _local_tokenizer.pad_token is None:
            _local_tokenizer.pad_token = _local_tokenizer.eos_token

        # Check if this is a LoRA adapter (has adapter_config.json)
        adapter_config = os.path.join(model_path, "adapter_config.json")
        if os.path.exists(adapter_config):
            logger.info("Detected LoRA adapter, loading with PEFT...")
            import json

            from peft import PeftModel

            with open(adapter_config) as f:
                adapter_info = json.load(f)
            base_model_path = adapter_info.get("base_model_name_or_path", "../bot-training/models/fastAPI_Model")

            base_model = AutoModelForCausalLM.from_pretrained(
                base_model_path,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
            )
            _local_model = PeftModel.from_pretrained(base_model, model_path)
            _local_model = _local_model.merge_and_unload()
        else:
            _local_model = AutoModelForCausalLM.from_pretrained(
                model_path,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
                low_cpu_mem_usage=True,
            )

        _local_model_path = model_path
        self._device = next(_local_model.parameters()).device
        self.model = os.path.basename(model_path)
        logger.info(f"Local model loaded: {self.model} on {self._device}")

    def complete(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 4096, **kwargs
    ) -> LLMResponse:
        self._load_model()

        import torch

        # Format messages using chat template
        chat_messages = [{"role": m.role, "content": m.content} for m in messages]

        text = _local_tokenizer.apply_chat_template(
            chat_messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )

        inputs = _local_tokenizer(text, return_tensors="pt").to(self._device)
        input_length = inputs["input_ids"].shape[1]

        start_time = time.time()

        with torch.no_grad():
            outputs = _local_model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=0.9,
                repetition_penalty=1.1,
                pad_token_id=_local_tokenizer.eos_token_id,
            )

        latency = (time.time() - start_time) * 1000

        output_tokens = outputs[0][input_length:]
        response_text = _local_tokenizer.decode(output_tokens, skip_special_tokens=True)

        return LLMResponse(
            content=response_text.strip(),
            model=self.model,
            input_tokens=input_length,
            output_tokens=len(output_tokens),
            total_tokens=input_length + len(output_tokens),
            finish_reason="stop",
            latency_ms=latency,
        )

    def stream(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 4096, **kwargs
    ) -> Generator[str, None, None]:
        # For simplicity, just yield the full response
        response = self.complete(messages, temperature, max_tokens, **kwargs)
        yield response.content

    def health_check(self) -> bool:
        try:
            self._find_model_path()
            return True
        except FileNotFoundError:
            return False


def unload_local_model():
    """Free GPU memory by unloading the local model."""
    global _local_model, _local_tokenizer, _local_model_path

    if _local_model is not None:
        import gc

        import torch
        del _local_model
        del _local_tokenizer
        _local_model = None
        _local_tokenizer = None
        _local_model_path = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        logger.info("Local model unloaded, GPU memory freed")
