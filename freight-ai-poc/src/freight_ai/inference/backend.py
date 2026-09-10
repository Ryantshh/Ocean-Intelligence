import json
from pathlib import Path
from typing import Protocol

from freight_ai.config import ModelConfig


class Backend(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str: ...


class ScriptedBackend:
    """Test double only. Never report its output as model inference."""

    def __init__(self, response):
        self.response = response

    def generate(self, messages):
        return self.response


class HFBackend:
    def __init__(self, config: ModelConfig):
        import torch
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            set_seed,
        )

        set_seed(config.seed)
        self.config = config
        device = config.device
        if device == "auto":
            device = (
                "cuda"
                if torch.cuda.is_available()
                else "mps"
                if torch.backends.mps.is_available()
                else "cpu"
            )
        if config.quantize_4bit and (device != "cuda" or not torch.cuda.is_available()):
            raise RuntimeError(
                "This POC's 4-bit backend requires CUDA and bitsandbytes. Use unquantized CPU/MPS inference here."
            )
        kwargs = {
            "revision": config.revision,
            "trust_remote_code": False,
            "local_files_only": config.local_files_only,
            "cache_dir": config.cache_dir,
        }
        model_source = config.model_id
        if config.local_files_only and not Path(model_source).is_dir():
            from huggingface_hub import snapshot_download

            model_source = snapshot_download(
                config.model_id,
                revision=config.revision,
                cache_dir=config.cache_dir,
                local_files_only=True,
            )
        self.tokenizer = AutoTokenizer.from_pretrained(model_source, **kwargs)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if not self.tokenizer.chat_template:
            raise ValueError("Model tokenizer needs an explicit chat template")
        if config.quantize_4bit:
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16
                if torch.cuda.is_bf16_supported()
                else torch.float16,
            )
            kwargs["device_map"] = {"": 0}
        self.model = AutoModelForCausalLM.from_pretrained(model_source, **kwargs)
        if not config.quantize_4bit:
            self.model.to(device)
        if config.adapter_path:
            from peft import PeftModel

            manifest = json.loads(
                (Path(config.adapter_path) / "freight_manifest.json").read_text()
            )
            if (
                manifest["model_id"] != config.model_id
                or manifest["revision"] != config.revision
            ):
                raise ValueError(
                    "Adapter base model/revision differs from inference configuration"
                )
            self.model = PeftModel.from_pretrained(
                self.model,
                config.adapter_path,
                local_files_only=config.local_files_only,
            )
        self.model.eval()
        self.metadata = {
            "backend": "transformers",
            **config.model_dump(),
            "device": device,
            "resolved_revision": getattr(self.model.config, "_commit_hash", None),
        }

    def generate(self, messages):
        import torch

        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        length = inputs["input_ids"].shape[-1]
        context = getattr(self.model.config, "max_position_embeddings", None)
        if length > self.config.max_input_tokens or (
            context and length + self.config.max_new_tokens > context
        ):
            raise ValueError(
                "Prompt exceeds token budget; context was not silently truncated"
            )
        inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=self.config.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
            )
        return self.tokenizer.decode(
            output[0, length:], skip_special_tokens=True
        ).strip()
