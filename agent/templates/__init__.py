# agent/templates/__init__.py
import logging
import json
from pathlib import Path

logger = logging.getLogger("ops-agent.templates")

_TEMPLATE_DIR = Path(__file__).parent / "cards"


def render_card(template_name: str, **kwargs) -> dict:
    """Render a Feishu card from template file"""
    template_path = _TEMPLATE_DIR / f"{template_name}.json"
    logger.debug(f"渲染卡片模板: {template_name}, 参数={list(kwargs.keys())}")

    try:
        with open(template_path) as f:
            template_text = f.read()

        for key, value in kwargs.items():
            escaped_value = json.dumps(str(value), ensure_ascii=False)[1:-1]
            placeholder = f"{{{{{key}}}}}"
            if placeholder not in template_text:
                logger.warning(f"模板 '{template_name}' 中缺少占位符 '{key}'")
            template_text = template_text.replace(placeholder, escaped_value)

        return json.loads(template_text)
    except FileNotFoundError:
        logger.error(f"卡片模板文件不存在: {template_path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"卡片模板 JSON 解析错误: {template_name}, 错误={e}")
        raise
