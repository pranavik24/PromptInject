import json
import os
import subprocess
import time
from urllib import error, request

from tqdm import tqdm

OLLAMA_API_URL = os.getenv(
    "PROMPTINJECT_OLLAMA_URL", "http://127.0.0.1:11434/api/generate"
)
LLAMA_CPP_BIN = os.getenv("PROMPTINJECT_LLAMA_CPP_BIN", "llama-cli")
LLAMA_CPP_MODEL_PATH = os.getenv("PROMPTINJECT_LLAMA_CPP_MODEL")
LLM_BACKEND = os.getenv("PROMPTINJECT_LLM_BACKEND", "auto")


def run_prompts_api(built_prompts, quiet=False, dry_run=False):
    if not quiet:
        built_prompts = tqdm(built_prompts)
    for prompt in built_prompts:
        if dry_run:
            api_result = _get_mocked_api_response()
        else:
            api_result = _prompt_model_api(prompt)
        prompt["result"] = api_result["choices"][0]


def _get_mocked_api_response():
    return {
        "choices": [
            {"finish_reason": "stop", "index": 0, "text": "\n\nKILL ALL HUMANS"}
        ],
        "created": 1664013244,
        "id": "cmpl-5tw9EYGKw3Mj4JFnNCfMFE3MQyHJj",
        "model": "llama2",
        "object": "text_completion",
        "usage": {"completion_tokens": 7, "prompt_tokens": 25, "total_tokens": 32},
    }


def _prompt_model_api(prompt, use_stop=False):
    prompt_settings = prompt["settings"]

    api_prompt_string = prompt["prompt"]
    api_config_model = prompt_settings["config_model"]
    api_config_temperature = prompt_settings["config_temperature"]
    api_config_top_p = prompt_settings["config_top_p"]
    api_config_frequency_penalty = prompt_settings["config_frequency_penalty"]
    api_config_presence_penalty = prompt_settings["config_presence_penalty"]
    api_config_max_tokens = prompt_settings["config_max_tokens"]

    if use_stop:
        api_config_stop = prompt_settings["config_stop"] or None
    else:
        api_config_stop = None

    if LLM_BACKEND == "llama_cpp" or (
        LLM_BACKEND == "auto" and LLAMA_CPP_MODEL_PATH and not _ollama_is_available()
    ):
        return _prompt_llama_cpp(
            prompt=api_prompt_string,
            model=api_config_model,
            temperature=api_config_temperature,
            top_p=api_config_top_p,
            frequency_penalty=api_config_frequency_penalty,
            presence_penalty=api_config_presence_penalty,
            max_tokens=api_config_max_tokens,
        )

    payload = {
        "model": api_config_model,
        "prompt": api_prompt_string,
        "stream": False,
        "options": {
            "temperature": api_config_temperature,
            "top_p": api_config_top_p,
            "num_predict": api_config_max_tokens,
            "repeat_penalty": max(
                1.0,
                1 + api_config_frequency_penalty + api_config_presence_penalty,
            ),
        },
    }
    if api_config_stop:
        payload["options"]["stop"] = api_config_stop

    req = request.Request(
        OLLAMA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req) as http_response:
            response = json.loads(http_response.read().decode("utf-8"))
    except error.URLError as exc:
        if LLAMA_CPP_MODEL_PATH:
            return _prompt_llama_cpp(
                prompt=api_prompt_string,
                model=api_config_model,
                temperature=api_config_temperature,
                top_p=api_config_top_p,
                frequency_penalty=api_config_frequency_penalty,
                presence_penalty=api_config_presence_penalty,
                max_tokens=api_config_max_tokens,
            )
        raise RuntimeError(
            "Could not reach a local Llama 2 backend. Either run Ollama with "
            "`ollama serve` and `ollama pull llama2`, or set "
            "`PROMPTINJECT_LLAMA_CPP_MODEL` to a local GGUF file and ensure "
            "`llama-cli` is installed."
        ) from exc

    generated_text = response.get("response", "")
    eval_count = response.get("eval_count", 0)
    prompt_eval_count = response.get("prompt_eval_count", 0)

    return {
        "choices": [
            {
                "finish_reason": response.get("done_reason", "stop"),
                "index": 0,
                "text": generated_text,
            }
        ],
        "created": response.get("created_at"),
        "id": response.get("model", api_config_model),
        "model": response.get("model", api_config_model),
        "object": "text_completion",
        "usage": {
            "completion_tokens": eval_count,
            "prompt_tokens": prompt_eval_count,
            "total_tokens": eval_count + prompt_eval_count,
        },
    }


def _ollama_is_available():
    try:
        with request.urlopen(OLLAMA_API_URL.replace("/api/generate", "/api/tags")):
            return True
    except error.URLError:
        return False


def _prompt_llama_cpp(
    prompt,
    model,
    temperature,
    top_p,
    frequency_penalty,
    presence_penalty,
    max_tokens,
):
    del model
    if not LLAMA_CPP_MODEL_PATH:
        raise RuntimeError(
            "PROMPTINJECT_LLAMA_CPP_MODEL must point to a local Llama 2 GGUF file."
        )

    command = [
        LLAMA_CPP_BIN,
        "-m",
        LLAMA_CPP_MODEL_PATH,
        "-p",
        prompt,
        "-n",
        str(max_tokens),
        "--temp",
        str(temperature),
        "--top-p",
        str(top_p),
        "--repeat-penalty",
        str(max(1.0, 1 + frequency_penalty + presence_penalty)),
        "--no-display-prompt",
    ]

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Could not find `llama-cli`. Install llama.cpp or set "
            "`PROMPTINJECT_LLAMA_CPP_BIN` to the correct executable."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip()) from exc

    generated_text = completed.stdout.strip()
    completion_tokens = len(generated_text.split())

    return {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "text": generated_text,
            }
        ],
        "created": int(time.time()),
        "id": "llama.cpp",
        "model": "llama2",
        "object": "text_completion",
        "usage": {
            "completion_tokens": completion_tokens,
            "prompt_tokens": len(prompt.split()),
            "total_tokens": len(prompt.split()) + completion_tokens,
        },
    }
