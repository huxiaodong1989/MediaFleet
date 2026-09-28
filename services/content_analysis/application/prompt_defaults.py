"""从仓库内种子文件构造首次发布的评课提示词方案。"""

from pathlib import Path

from media_platform.contracts.content_evaluation import (
    PromptBundleContent,
    PromptStepDefinition,
)


_PROMPT_DIR = Path(__file__).resolve().parents[1] / "bootstrap_prompts"

_STEPS = (
    ("CLASSROOM_SUMMARY", "课堂摘要", 1, "01_classroom_summary.md", "请对提供的课堂录音文本、行为分析进行分析，生成课堂摘要。", False),
    ("FREQUENCY_ANALYSIS", "词频与语速分析", 2, "02_frequency_analysis.md", "请对课堂录音文本进行词频与语速分析。", False),
    ("KEYEVENT_ANALYSIS", "关键事件分析", 3, "03_keyevent_analysis.md", "请对课堂录音和行为分析进行关键事件分析。", False),
    ("CONTENT_SUMMARY", "课堂总体评价与特征提炼", 4, "04_content_summary.md", "请基于课堂内容进行总体评价与特征提炼。", False),
    ("QA_ANALYSIS", "课堂问答类型与频次统计", 5, "05_qa_analysis.md", "请对课堂问答进行类型与频次统计分析。", False),
    ("TEACHING_METHOD", "讲授方式占比统计", 6, "06_teaching_method.md", "请对课堂讲授方式进行占比统计分析。", False),
    ("KNOWLEDGE_GRAPH", "课堂知识图谱构建", 7, "07_knowledge_graph.md", "请基于课程内容构建知识图谱。", False),
    ("CLASSROOM_SCORE", "课堂评分", 8, "08_classroom_score.md", "请对课堂教学进行量化评分。", True),
)


def load_bootstrap_prompt_bundle(model: str) -> PromptBundleContent:
    """加载只用于首次入库的本地提示词，运行任务不直接读取这些文件。"""

    steps = [
        PromptStepDefinition(
            code=code,
            name=name,
            order=order,
            system_prompt=(_PROMPT_DIR / filename).read_text(encoding="utf-8"),
            user_prompt=user_prompt,
            model=model,
            temperature=0.5,
            max_tokens=32768,
            critical=critical,
        )
        for code, name, order, filename, user_prompt, critical in _STEPS
    ]
    subtitle_path = _PROMPT_DIR / "00_subtitle_edit_operations.md"
    return PromptBundleContent(
        steps=steps,
        subtitle_edit_prompt=subtitle_path.read_text(encoding="utf-8"),
    )


__all__ = ["load_bootstrap_prompt_bundle"]
