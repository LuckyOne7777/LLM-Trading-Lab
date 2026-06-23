from openai import OpenAI
import os
import pandas as pd
import anthropic
from datetime import datetime
from ..prompts.prompt_assembly import *
from ..prompts.starting_prompt import create_starting_prompt

def prompt_deepseek(text: str, model: str) -> str:

    deepseek_client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",)
    
    response = deepseek_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": text}],
        temperature=0.0,
    )

    if not response.choices:
        raise RuntimeError("No choices returned from DeepSeek.")

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("Output from DeepSeek was None.")

    return content

def prompt_chatgpt(text: str, model: str) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": text}],
        temperature=0.0,
    )

    if not response.choices:
        raise RuntimeError("No choices returned from ChatGPT.")

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("Output from ChatGPT was None.")

    return content

def prompt_claude(text: str, model: str) -> str:

    client = anthropic.Anthropic()
    client.api_key = os.environ["CLAUDE_API_KEY"]

    message = client.messages.create(
    model=model,
    max_tokens=10_000,
    messages=[{
        "role": "user",
        "content": text
    }]
    )

    content = message.content[0].text
    if content is None:
        raise RuntimeError("Output from Claude was None.")

    return content

def prompt_grok(text: str, model: str) -> str:

    client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "user", "content": text},
        ],
    )
    if response is None:
        raise RuntimeError("Response from Grok was None.")
    if not response.output_text:
        raise RuntimeError("Output text from Grok was None.")
    return response.output_text

def prompt_deep_research(skeleton, libb) -> tuple[str, str]:

    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")
    text = create_deep_research_prompt(skeleton, libb)
        
    if "deepseek" in model:
        return prompt_deepseek(text, model), text
    elif "gpt" in model:
        return prompt_chatgpt(text, model), text
    elif "claude" in model:
        return prompt_claude(text, model), text
    elif "grok" in model:
        return prompt_grok(text, model), text
    else:
        raise RuntimeError(f"Unidentified model: {model}")

def prompt_daily_report(skeleton, libb) -> tuple[str, str]:

    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")
    text = create_daily_prompt(skeleton, libb)
    if "deepseek" in model:
        return prompt_deepseek(text, model), text
    elif "gpt" in model:
        return prompt_chatgpt(text, model), text
    elif "claude" in model:
        return prompt_claude(text, model), text
    elif "grok" in model:
        return prompt_grok(text, model), text
    else:
        raise RuntimeError(f"Unidentified model: {model}")
    
def prompt_starting_report(prompt: str, libb: LIBBmodel):

    model = libb._model_path.replace("multi_model_ipo/artifacts/", "")

    if "deepseek" in model:
        return prompt_deepseek(prompt, model)
    elif "gpt" in model:
        return prompt_chatgpt(prompt, model)
    elif "claude" in model:
        return prompt_claude(prompt, model)
    elif "grok" in model:
        return prompt_grok(prompt, model)

    else:
        raise RuntimeError(f"Unidentified model: {model}")