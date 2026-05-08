from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

templates = Jinja2Templates(directory="templates")

def create_app() -> FastAPI:
    app = FastAPI(title="Alana Dashboard API")

    # mount static and templates
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # import routers lazily to avoid circular imports at package import time
    from .routers import metrics as metrics_router
    from .routers import packages as packages_router
    from .routers import finance as finance_router
    from .routers import customers as customers_router
    from . import startup

    # include routers
    app.include_router(metrics_router.router)
    app.include_router(packages_router.router)
    app.include_router(finance_router.router)
    app.include_router(customers_router.router)

    # startup tasks
    startup.init_app(app)

    return app


app = create_app()

"""Compatibility package to preserve legacy ASGI path app.main:app."""

"""Compatibility shim package 'app' for legacy tests."""

__all__ = []

