"""Retired advertising module: keep only an empty response for older clients."""
from fastapi import APIRouter

from common.schemas.common import ApiResponse

router = APIRouter(tags=["advertisements"])


@router.get("/public", response_model=ApiResponse)
async def get_public_ads() -> ApiResponse:
    """旧客户端仍可读取此地址，当前版本不提供广告内容。"""
    return ApiResponse(success=True, data={"carousel": [], "text": []})
