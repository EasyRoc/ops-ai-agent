# agent/llm/client.py
from openai import AsyncOpenAI

from agent.config import settings

client = AsyncOpenAI(
    base_url=settings.deepseek_base_url,
    api_key=settings.deepseek_api_key,
)


async def chat(prompt: str, system: str = None, model: str = None) -> str:
    """Send a single-turn chat, return text response"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=model or settings.deepseek_model,
        messages=messages,
        temperature=0.1,
    )
    return response.choices[0].message.content


async def chat_json(prompt: str, system: str = None, model: str = None) -> dict:
    """Send a chat, require JSON response"""
    system_msg = (system or "") + "\nYou MUST respond with valid JSON only, no markdown, no explanation."
    text = await chat(prompt, system=system_msg, model=model)
    import json
    # Handle possible markdown code block wrapping
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    return json.loads(text)
