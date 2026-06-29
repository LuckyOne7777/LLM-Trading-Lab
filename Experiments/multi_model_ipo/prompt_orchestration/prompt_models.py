import os

import litellm
from ..prompts.prompt_assembly import *
from ..prompts.starting_prompt import create_starting_prompt

# Bridge legacy env var so existing CLAUDE_API_KEY setups keep working
if "ANTHROPIC_API_KEY" not in os.environ and "CLAUDE_API_KEY" in os.environ:
    os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_API_KEY"]


def _to_litellm_model(model: str) -> str:
    if model.startswith("deepseek"):
        return f"deepseek/{model}"
    if model.startswith("grok"):
        return f"xai/{model}"
    return model


def prompt_model(text: str, model: str) -> str:
    response = litellm.completion(
        model=_to_litellm_model(model),
        messages=[{"role": "user", "content": text}],
        temperature=0.0,
        drop_params=True,
    )

    if not response.choices:
        raise RuntimeError(f"No choices returned from {model}.")

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError(f"Output from {model} was None.")

    return content


def prompt_deep_research(skeleton, libb) -> tuple[str, str]:
    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")
    text = create_deep_research_prompt(skeleton, libb)
    return prompt_model(text, model), text


def prompt_daily_report(skeleton, libb) -> tuple[str, str]:
    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")
    text = create_daily_prompt(skeleton, libb)
    return prompt_model(text, model), text


def prompt_starting_report(prompt: str, libb):
    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")
    return prompt_model(prompt, model)