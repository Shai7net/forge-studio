"""FIBO API uses embedded images and respects Forge's --api-auth setting."""

import secrets
from typing import List, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from . import models, service
from .contracts import Generation, decode_image, encode_image
from .runner import Cancelled, runner


class GenerateRequest(BaseModel):
    variant: str = "dev"
    mode: str = "generate"
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 0
    cfg: float = 0
    seed: int = -1
    count: int = 1
    precision: str = "int8"
    prompt_writer: str = "off"
    images: List[str] = Field(default_factory=list, max_items=4)
    mask: Optional[str] = None
    loras: List[str] = Field(default_factory=list, max_items=4)
    lora_weight: float = 1.0


def auth_dependency(auth_config):
    if not auth_config:
        return []
    credentials = [tuple(entry.split(":", 1)) for entry in auth_config.split(",")]
    basic = HTTPBasic()

    def authenticate(value: HTTPBasicCredentials = Depends(basic)):
        for user, password in credentials:
            if secrets.compare_digest(value.username.encode(), user.encode()) and secrets.compare_digest(value.password.encode(), password.encode()):
                return
        raise HTTPException(status_code=401, detail="Incorrect credentials", headers={"WWW-Authenticate": "Basic"})

    return [Depends(authenticate)]


def register(app, auth_config=None):
    dependencies = auth_dependency(auth_config)

    @app.get("/fibo2/status", dependencies=dependencies)
    def status():
        return {**models.status(), "job": runner.status()}

    @app.post("/fibo2/generate", dependencies=dependencies)
    def generate(payload: GenerateRequest):
        from PIL import Image
        try:
            params = payload.dict(exclude={"images", "mask", "loras", "lora_weight"})
            refs = [decode_image(data).convert("RGB") for data in payload.images]
            mask = decode_image(payload.mask).convert("L") if payload.mask is not None else None
            response = service.generate(Generation(**params), refs, mask, payload.loras, payload.lora_weight)
            encoded = []
            for item in response["images"]:
                with Image.open(item["path"]) as image:
                    encoded.append(encode_image(image))
            return {"images": encoded, "info": response}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Cancelled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/fibo2/interrupt", dependencies=dependencies)
    def interrupt():
        return runner.cancel()
