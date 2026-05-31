# agent/templates/__init__.py
import json
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "cards"


def render_card(template_name: str, **kwargs) -> dict:
    """Render a Feishu card from template file"""
    with open(_TEMPLATE_DIR / f"{template_name}.json") as f:
        template_text = f.read()

    for key, value in kwargs.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", str(value))

    return json.loads(template_text)
