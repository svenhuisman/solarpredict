"""Vercel entrypoint — exposes the FastAPI ASGI app."""

from app.main import app

__all__ = ["app"]
