"""本地默认提示词仅用于首次数据库初始化，不能作为运行时故障回退。"""

from pathlib import Path

from media_platform.contracts.image_analysis import ImagePromptBundleContent


_PROMPT_PATH = Path(__file__).resolve().parent / "bootstrap_prompts" / "prompt.md"


def load_bootstrap_prompt_bundle(model: str) -> ImagePromptBundleContent:
    return ImagePromptBundleContent(
        prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        model=model,
        temperature=0.2,
        max_tokens=4096,
        output_schema_version="image-metrics.v1",
        metric_definition_version="classroom-behavior.v1",
    )
