# agent/llm/client.py
import logging
import time
from openai import AsyncOpenAI

from agent.config import settings

logger = logging.getLogger("ops-agent.llm")

client = AsyncOpenAI(
    base_url=settings.deepseek_base_url,
    api_key=settings.deepseek_api_key,
)


async def chat(prompt: str, system: str = None, model: str = None) -> str:
    """Send a single-turn chat, return text response"""
    model_name = model or settings.deepseek_model
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    logger.info(f"LLM 调用: 模型={model_name}, 提示词长度={len(prompt)}, System提示词={'有' if system else '无'}")
    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.1,
        )
        elapsed = time.monotonic() - start
        content = response.choices[0].message.content
        usage = response.usage
        logger.info(
            f"LLM 调用完成: 模型={model_name}, 耗时={elapsed:.2f}s, "
            f"输入Token={usage.prompt_tokens if usage else '?'}, "
            f"输出Token={usage.completion_tokens if usage else '?'}"
        )
        return content
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.error(f"LLM 调用失败: 模型={model_name}, 耗时={elapsed:.2f}s, 错误={e}")
        raise


async def chat_json(prompt: str, system: str = None, model: str = None) -> dict:
    """Send a chat, require JSON response"""
    system_msg = (system or "") + "\nYou MUST respond with valid JSON only, no markdown, no explanation."
    logger.info(f"LLM JSON调用: 提示词长度={len(prompt)}")
    text = await chat(prompt, system=system_msg, model=model)
    import json
    # Handle possible markdown code block wrapping
    text = text.strip()
    if text.startswith("```"):
        logger.debug("正在去除 LLM JSON 响应中的 markdown 代码块标记")
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    result = json.loads(text)
    logger.info(f"LLM JSON调用完成: 结果字段={list(result.keys())}")
    return result
