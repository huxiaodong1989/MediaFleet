import asyncio

from services.content_analysis.infrastructure.clients import BehaviorAnalysisClient


def test_behavior_analysis_without_endpoint_is_explicitly_degraded():
    result = asyncio.run(
        BehaviorAnalysisClient(None).get(
            classroom_id="classroom-1",
            tenant_id=88888,
        )
    )

    assert result.available is False
    assert result.data is None
    assert result.missing_reason == "not_configured"
