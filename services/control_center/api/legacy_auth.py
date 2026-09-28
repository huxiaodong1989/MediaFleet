"""旧 master API 使用的 API Key 认证。

该模块承接历史路由的认证行为：客户端仍通过 ``X-API-Key`` 传入内部密钥。
配置在请求校验时懒加载，避免导入路由模块时因为本地 `.env` 未准备好直接失败。
"""

from secrets import compare_digest

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from media_platform.common.config import get_settings


# 定义API密钥头部
X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(X_API_KEY)):
    """验证历史 API 调用方的内部密钥并返回兼容身份信息。"""

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少API密钥"
        )

    settings = get_settings()
    expected_key = str(settings.API_KEY or "")
    if not expected_key or not compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的API密钥"
        )

    return {"name": "默认服务", "tenant_id": "default"}
