"""Isolated operator service sharing the existing database and authentication."""
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend-web"))
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from loguru import logger
from app.core.config import get_settings
from app.services.jwt_secret_service import ensure_jwt_secret_key
from common.utils.internal_token_service import load_internal_api_token
from common.db.redis_client import initialize_redis_client,close_redis_client
from common.db.session import async_engine
from operations import store
from operations.engine import worker
from operations.api import router
from operations.workspace_api import router as workspace_router


@asynccontextmanager
async def lifespan(app):
    await ensure_jwt_secret_key(get_settings())
    await load_internal_api_token(get_settings())
    await initialize_redis_client()
    await store.init()
    task=asyncio.create_task(worker())
    from operations.recovery import worker as recovery_worker
    recovery_task=asyncio.create_task(recovery_worker())
    from operations.workflows import worker as workflow_worker
    workflow_task=asyncio.create_task(workflow_worker())
    app.state.worker=task
    app.state.recovery_worker=recovery_task
    app.state.workflow_worker=workflow_task
    try:
        yield
    finally:
        task.cancel()
        recovery_task.cancel()
        workflow_task.cancel()
        await asyncio.gather(task,recovery_task,workflow_task,return_exceptions=True)
        await close_redis_client()
        await async_engine.dispose()


app=FastAPI(title="闲鱼商品运营助手",version="0.1.0",lifespan=lifespan)
app.include_router(router)
app.include_router(workspace_router)


@app.exception_handler(ValueError)
async def invalid_input(request:Request,exc:ValueError):
    return JSONResponse(status_code=400,content={"detail":str(exc)[:500]})


@app.get("/health")
async def health():
    alive=all(getattr(app.state,key,None) and not getattr(app.state,key).done() for key in ('worker','recovery_worker','workflow_worker'))
    payload={'status':'ok' if alive else 'degraded','service':'operations','version':'0.1.0',
             **{key:'running' if getattr(app.state,key,None) and not getattr(app.state,key).done() else 'stopped' for key in ('worker','recovery_worker','workflow_worker')}}
    return JSONResponse(status_code=200 if alive else 503,content=payload)


if __name__ == "__main__":
    import uvicorn
    logger.remove()
    # Existing search client logs entire responses; suppress that module here.
    logger.add(sys.stderr,level="INFO",filter=lambda r: r["name"] not in {"common.services.xianyu_search_client"})
    uvicorn.run(app,host="0.0.0.0",port=9010,access_log=False)
