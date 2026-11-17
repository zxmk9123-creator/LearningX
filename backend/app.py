"""Dedicated Vercel FastAPI entrypoint.

Vercel must use backend/ as its Root Directory in a separate project.
Do not make this app the entrypoint for the static LearningX frontend.
"""
import os

# Reliable platform marker independent of Vercel System Env exposure settings.
# The existing lesson adapter uses local paths, so disallow course-material
# uploads until signed evidence is stored in private durable object storage.
os.environ["NIT_SERVERLESS_RUNTIME"] = "1"

from learningx.api import app  # noqa: E402
