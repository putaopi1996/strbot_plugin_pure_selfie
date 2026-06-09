"""Google Gemini native generateContent API backend for image generation/editing.

Uses the google-genai SDK directly instead of OpenAI compatibility layer.
This avoids the 'Unhandled generated data mime type' error that occurs
when using Gemini's OpenAI-compat chat/completions endpoint for image output.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

from .image_format import guess_image_mime_and_ext

logger = logging.getLogger(__name__)


class GeminiNativeBackend:
    """Image generation/edit via Google Gemini native generateContent API."""

    def __init__(
        self,
        *,
        imgr,
        api_key: str,
        model: str = "gemini-2.5-flash-image",
        timeout: int = 120,
    ):
        self.imgr = imgr
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except ImportError:
                raise RuntimeError(
                    "google-genai SDK not installed. Run: pip install google-genai"
                )
        return self._client

    async def close(self) -> None:
        self._client = None

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        *,
        model: str | None = None,
        size: str | None = None,
        **kwargs,
    ) -> Path:
        """Generate/edit image using Gemini native API with reference images."""
        import asyncio

        final_model = model or self.model
        if not final_model:
            raise RuntimeError("\u672a\u914d\u7f6e\u6a21\u578b")

        client = self._get_client()

        # Build content parts: text prompt + reference images
        from google.genai import types

        parts = []

        # Add reference images as inline data
        for img_bytes in images:
            mime, _ = guess_image_mime_and_ext(img_bytes)
            parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type=mime)
            )

        # Add text prompt
        parts.append(types.Part.from_text(text=prompt))

        # Configure generation to output images
        generate_config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        )

        t0 = time.time()
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=final_model,
                contents=parts,
                config=generate_config,
            )
        except Exception as exc:
            logger.error(
                "[GeminiNative] API call failed, model=%s, elapsed=%.2fs: %s",
                final_model,
                time.time() - t0,
                exc,
            )
            raise

        logger.info("[GeminiNative] API response elapsed: %.2fs", time.time() - t0)

        # Extract image from response
        if not response.candidates:
            raise RuntimeError("\u6a21\u578b\u672a\u8fd4\u56de\u4efb\u4f55\u5019\u9009\u7ed3\u679c")

        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                    if isinstance(image_data, str):
                        image_data = base64.b64decode(image_data)
                    if image_data and len(image_data) > 100:
                        return await self.imgr.save_image(image_data)

        raise RuntimeError(
            "\u6a21\u578b\u672a\u8fd4\u56de\u56fe\u7247\u6570\u636e\uff0c\u53ef\u80fd\u88ab\u5b89\u5168\u8fc7\u6ee4\u5668\u62e6\u622a"
        )

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str | None = None,
        **kwargs,
    ) -> Path:
        """Generate image from text only."""
        return await self.edit(prompt, [], model=model, size=size, **kwargs)
