"""旧 master API 使用的 API Key 认证。

该模块承接历史路由的认证行为：客户端仍通过 ``X-API-Key`` 传入内部密钥。
配置在请求校验时懒加载，避免导入路由模块时因为本地 `.env` 未准备好直接失败。
"""

from fastapi import Request

from services.control_center.api.dependencies import verify_internal_api_key


async def verify_api_key(request: Request):
    """验证历史 API 调用方的内部密钥并返回兼容身份信息。"""

    principal = verify_internal_api_key(request, request.headers.get("X-API-Key"))
    return {
        "name": principal.display_name,
        "caller_id": principal.caller_id,
        "tenant_id": "default",
    }
