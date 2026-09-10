import importlib.metadata
from pathlib import Path

from freight_ai.data.ingest import write_json

from .dataset import read_split, verify_dataset


def train(config):
    # Validation is permitted during fitting; held-out test examples never go to Trainer.
    manifest = verify_dataset(config["training_data"])
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "QLoRA training in this POC requires a CUDA GPU. CPU/MPS inference remains available; no adapter has been trained."
        )
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        set_seed,
    )
    from trl import SFTConfig, SFTTrainer

    mc, tc = config["model"], config["training"]
    if mc.get("adapter_path"):
        raise ValueError("Start training from the base model; clear adapter_path")
    output = Path(tc["output_dir"])
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "Adapter output already exists; choose a fresh experiment directory"
        )
    set_seed(tc["seed"])
    bf16 = torch.cuda.is_bf16_supported()
    tokenizer = AutoTokenizer.from_pretrained(
        mc["model_id"], revision=mc["revision"], trust_remote_code=False
    )
    if not tokenizer.chat_template:
        raise ValueError("Training requires a model chat template")
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = "right"
    model = AutoModelForCausalLM.from_pretrained(
        mc["model_id"],
        revision=mc["revision"],
        trust_remote_code=False,
        device_map={"": torch.cuda.current_device()},
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if bf16 else torch.float16,
        ),
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.config.use_cache = False
    lora = LoraConfig(
        r=tc["lora_r"],
        lora_alpha=tc["lora_alpha"],
        lora_dropout=tc["lora_dropout"],
        target_modules=tc["target_modules"],
        bias="none",
        task_type="CAUSAL_LM",
    )

    def dataset(split):
        rows = read_split(config["training_data"], split)
        for row in rows:
            tokens = tokenizer.apply_chat_template(
                row["prompt"] + row["completion"], tokenize=True
            )
            if len(tokens) > tc["max_length"]:
                raise ValueError(
                    f"Training example {row['id']} exceeds max_length; refusing label truncation"
                )
        return Dataset.from_list(
            [{k: r[k] for k in ("prompt", "completion")} for r in rows]
        )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        peft_config=lora,
        train_dataset=dataset("train"),
        eval_dataset=dataset("validation"),
        args=SFTConfig(
            output_dir=tc["checkpoint_dir"],
            num_train_epochs=tc["epochs"],
            learning_rate=tc["learning_rate"],
            per_device_train_batch_size=tc["batch_size"],
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=tc["gradient_accumulation_steps"],
            max_length=tc["max_length"],
            completion_only_loss=True,
            packing=False,
            bf16=bf16,
            fp16=not bf16,
            gradient_checkpointing=True,
            eval_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=2,
            logging_steps=1,
            report_to="none",
            seed=tc["seed"],
            data_seed=tc["seed"],
        ),
    )
    result = trainer.train()
    validation = trainer.evaluate()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(output)
    write_json(
        output / "freight_manifest.json",
        {
            "model_id": mc["model_id"],
            "revision": mc["revision"],
            "resolved_revision": getattr(model.config, "_commit_hash", None),
            "training": tc,
            "dataset": manifest,
            "train_metrics": result.metrics,
            "validation_metrics": validation,
            "versions": {
                p: importlib.metadata.version(p)
                for p in ("torch", "transformers", "peft", "trl", "bitsandbytes")
            },
        },
    )
    return str(output)
