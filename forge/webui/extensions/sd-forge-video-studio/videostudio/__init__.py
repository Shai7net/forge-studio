"""Video Studio: Wan 2.2 text / image / first+last frame to video inside Forge, built for 8 GB cards."""

import os

NAME = "Video Studio"
EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI_DIR = os.path.dirname(os.path.dirname(EXT_DIR))
MODELS_DIR = os.path.join(WEBUI_DIR, "models", "VideoStudio")
HOME_DIR = os.path.join(WEBUI_DIR, "video-studio")
ENV_DIR = os.path.join(HOME_DIR, "env")
CACHE_DIR = os.path.join(HOME_DIR, "cache")
JOBS_DIR = os.path.join(HOME_DIR, "jobs")
OUTPUT_DIR = os.path.join(WEBUI_DIR, "output", "video-studio")
WORKER = os.path.join(EXT_DIR, "videostudio", "engine", "worker.py")
