"""FastAPI 应用入口 —— 只做三件事：校验配置、建表、挂路由。

为什么用 lifespan 而不是 @app.on_event("startup")？
    后者在 FastAPI 0.93 之后已废弃，而且「启动」和「关闭」分散在两个装饰器里，
    容易只写一个忘了配对。lifespan 用一个 asynccontextmanager 把一进一出收在一处。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings, validate_settings
from db import init_db
from routers import targets as targets_router
from routers import agent_run as agent_run_router
from routers import trace as trace_router
from routers import stats as stats_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """yield 之前 = 启动阶段，yield 之后 = 关闭阶段，中间这段才是服务真正在跑。"""

    # ① fail-fast：配置不全就别起。
    #    否则会变成「服务能起来、第一个请求才 401」——这种半死状态最难排查。
    validate_settings()

    # ② 建表：create_all 只建「还不存在的表」，已存在的一个字不改。
    #    所以改字段它管不了，那要靠 Alembic 迁移（W3 再上）。
    init_db()

    yield

    # ③ 关闭钩子。本期没有要释放的资源；将来 Redis 连接池、浏览器实例在这里关。
    #    它必须和 ② 成对存在 —— 只开不关就是内存泄漏。


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 收编业务路由。include_router 只是把子路由表挂上来，不会执行任何业务代码。
app.include_router(targets_router.router)
app.include_router(trace_router.router)
app.include_router(agent_run_router.router)
app.include_router(stats_router.router)

# 前端静态资源。挂载整个目录而不是一个个加端点：前端有 html/js/css 三个文件，
# 每加一个文件就加一个路由不现实。静态文件不碰数据库，和业务路由互不影响。
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """根路径直接给前端页面。

    include_in_schema=False：这是给人看的页面，不是给机器调的 API，
    塞进 /docs 只会让接口列表变脏。
    """
    return FileResponse("static/index.html")


@app.get("/health")
def health() -> dict:
    """健康检查：不碰数据库、不碰 LLM，只回答「进程还活着吗」。

    为什么要它？容器探针和部署脚本靠它判断服务起没起。
    它必须比任何业务接口都轻 —— 一旦它依赖数据库，库挂了就分不清
    是「服务死了」还是「库死了」。
    """
    return {"status": "ok", "app": settings.app_name}
