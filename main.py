from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image
from astrbot.api.star import Context, Star, StarTools

from .core.emoji_feedback import mark_failed, mark_processing, mark_success
from .core.image_manager import ImageManager
from .core.openai_chat_image_backend import OpenAIChatImageBackend
from .core.openai_compat_backend import OpenAICompatBackend
from .core.uploaded_refs import UploadedRefsManager
from .core.utils import close_session


@dataclass(slots=True)
class SendImageResult:
    ok: bool
    reason: str = ""
    last_error: str = ""

    def __bool__(self) -> bool:
        return self.ok


class GiteeAIImagePlugin(Star):
    IMAGE_AS_FILE_THRESHOLD_BYTES: int = 20 * 1024 * 1024

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config if isinstance(config, dict) else {}
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_gitee_aiimg")
        self.imgr: ImageManager | None = None
        self._refs_manager: UploadedRefsManager | None = None
        self._concurrency_lock = asyncio.Lock()
        self._image_inflight: dict[str, int] = {}
        self._minimal_selfie_backend: OpenAICompatBackend | None = None
        self._minimal_selfie_chat_backend: OpenAIChatImageBackend | None = None
        self._minimal_selfie_quota_lock = asyncio.Lock()

    async def initialize(self):
        self.imgr = ImageManager(self.config, self.data_dir)

        # Initialize UploadedRefsManager and sync reference images
        self._refs_manager = UploadedRefsManager(Path(self.data_dir) / "uploaded_refs")
        reference_images = self.config.get("minimal_selfie", {}).get("reference_images", [])
        if not isinstance(reference_images, list):
            reference_images = []
        sync_result = self._refs_manager.sync(reference_images)
        logger.info(
            "[PureSelfie] refs synced: %d files, %d bytes total",
            sync_result.total_files,
            sync_result.total_bytes,
        )

        # Detect legacy fields and log deprecation warning
        raw_minimal = self.config.get("minimal_selfie") or {}
        legacy_fields = [
            f for f in ("reference_image_urls", "reference_image_files", "reference_image_dir")
            if f in raw_minimal and raw_minimal[f]
        ]
        if legacy_fields:
            logger.warning(
                "[PureSelfie] Deprecated config fields detected: %s. "
                "These fields are ignored; use 'reference_images' (WebUI file upload) instead.",
                ", ".join(legacy_fields),
            )

        conf = self._get_minimal_selfie_config()
        logger.info(
            "[PureSelfie] enabled=%s groups=%s refs=%d model=%s image_size=%s",
            conf["enabled"],
            conf["enabled_groups"],
            sync_result.total_files,
            conf["model"] or "<empty>",
            conf["image_size"],
        )

    async def terminate(self):
        try:
            backend = getattr(self, "_minimal_selfie_backend", None)
            if backend is not None and hasattr(backend, "close"):
                await backend.close()
        except Exception:
            pass
        try:
            backend = getattr(self, "_minimal_selfie_chat_backend", None)
            if backend is not None and hasattr(backend, "close"):
                await backend.close()
        except Exception:
            pass
        if self.imgr is not None:
            await self.imgr.close()
        await close_session()

    @staticmethod
    def _as_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "off", "disable", "disabled", ""}:
                return False
        return default

    @staticmethod
    def _normalize_minimal_selfie_api_base_url(base_url: str) -> str:
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            return ""
        try:
            parts = urlsplit(normalized)
        except Exception:
            return normalized
        if (parts.hostname or "").lower() != "generativelanguage.googleapis.com":
            return normalized
        if (parts.path or "").rstrip("/").endswith("/openai"):
            return normalized
        return f"{normalized}/openai"

    @staticmethod
    def _is_google_official_openai_base_url(base_url: str) -> bool:
        try:
            parts = urlsplit(str(base_url or "").strip())
        except Exception:
            return False
        return (parts.hostname or "").lower() == "generativelanguage.googleapis.com"

    @staticmethod
    def _normalize_minimal_selfie_image_size(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text or text == "auto":
            return "auto"
        return str(value).strip()

    @staticmethod
    def _resolve_minimal_selfie_size_arg(conf: dict[str, Any]) -> str | None:
        value = str(conf.get("image_size", "auto") or "auto").strip().lower()
        if value == "auto":
            return None
        resolved = str(conf.get("image_size", "") or "").strip()
        return resolved or None

    def _get_minimal_selfie_config(self) -> dict[str, Any]:
        raw = self.config.get("minimal_selfie") or {}
        enabled_groups = [
            str(item).strip()
            for item in raw.get("enabled_groups", []) or []
            if str(item).strip()
        ]

        ignore_keywords = [
            s
            for item in raw.get("ignore_keywords", []) or []
            if (s := str(item).strip())
        ]

        group_rules: list[dict[str, Any]] = []
        for item in raw.get("group_rules", []) or []:
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("group_id", "") or "").strip()
            if not group_id:
                continue
            try:
                daily_limit = max(0, int(item.get("daily_limit", 0) or 0))
            except (TypeError, ValueError):
                daily_limit = 0
            group_rules.append(
                {
                    "group_id": group_id,
                    "daily_limit": daily_limit,
                    "limit_reject_prompt": str(
                        item.get("limit_reject_prompt", "") or ""
                    ).strip(),
                }
            )

        return {
            "enabled": self._as_bool(raw.get("enabled", True), default=True),
            "enabled_groups": enabled_groups,
            "preset_prompt": str(raw.get("preset_prompt", "") or "").strip(),
            "api_base_url": self._normalize_minimal_selfie_api_base_url(
                str(raw.get("api_base_url", "") or "").strip()
            ),
            "model": str(raw.get("model", "") or "").strip(),
            "api_token": str(raw.get("api_token", "") or "").strip(),
            "image_size": self._normalize_minimal_selfie_image_size(
                raw.get("image_size", "auto")
            ),
            "ignore_keywords": ignore_keywords,
            "group_rules": group_rules,
        }

    def _load_minimal_selfie_reference_file_bytes(self) -> list[bytes]:
        if self._refs_manager is None:
            raise RuntimeError("UploadedRefsManager is not initialized")
        images = self._refs_manager.load_reference_bytes()
        if not images:
            raise RuntimeError("\u672a\u914d\u7f6e\u53c2\u8003\u56fe\u6587\u4ef6")
        return images

    def _is_minimal_selfie_mode(self) -> bool:
        return bool(self._get_minimal_selfie_config()["enabled"])

    def _is_group_enabled_for_minimal_selfie(self, group_id: str) -> bool:
        gid = str(group_id or "").strip()
        if not gid:
            return False
        conf = self._get_minimal_selfie_config()
        return conf["enabled"] and gid in conf["enabled_groups"]

    def _parse_selfie_judge_result(self, text: str) -> tuple[bool, str]:
        raw = str(text or "").strip()
        if not raw:
            return False, ""
        try:
            payload = json.loads(raw)
        except Exception:
            return False, ""
        if not bool(payload.get("generate", False)):
            return False, ""
        return True, str(payload.get("prompt", "") or "").strip()

    def _build_minimal_selfie_prompt(self, llm_prompt: str) -> str:
        conf = self._get_minimal_selfie_config()
        parts = [
            "Generate one realistic selfie photo of the same person shown in the reference images.",
            "Keep facial identity, hairstyle, and overall vibe consistent with the references.",
        ]
        if conf["preset_prompt"]:
            parts.append(f"Preset requirements:\n{conf['preset_prompt']}")
        if str(llm_prompt or "").strip():
            parts.append(f"Dynamic user intent:\n{str(llm_prompt).strip()}")
        ref_count = len(self._refs_manager.list_reference_files()) if self._refs_manager else 0
        if ref_count > 0:
            parts.append(
                f"Reference image files are attached separately ({ref_count} files)."
            )
        parts.append(
            "Return one finished selfie image only. No collage, no text overlay, no watermark."
        )
        return "\n\n".join(parts)

    def _get_minimal_selfie_group_rule(self, group_id: str) -> dict[str, Any] | None:
        gid = str(group_id or "").strip()
        if not gid:
            return None
        for rule in self._get_minimal_selfie_config()["group_rules"]:
            if str(rule.get("group_id", "") or "").strip() == gid:
                return rule
        return None

    @staticmethod
    def _get_beijing_today_key() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    def _get_minimal_selfie_daily_counter_path(self) -> Path:
        return Path(self.data_dir) / "minimal_selfie_daily_counts.json"

    def _load_minimal_selfie_daily_counts(self) -> dict[str, dict[str, int]]:
        path = self._get_minimal_selfie_daily_counter_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        normalized: dict[str, dict[str, int]] = {}
        for day, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            normalized_bucket: dict[str, int] = {}
            for gid, count in bucket.items():
                try:
                    normalized_bucket[str(gid)] = max(0, int(count))
                except (TypeError, ValueError):
                    continue
            normalized[str(day)] = normalized_bucket
        return normalized

    def _save_minimal_selfie_daily_counts(self, counts: dict[str, dict[str, int]]) -> None:
        path = self._get_minimal_selfie_daily_counter_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(counts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_minimal_selfie_group_usage(self, group_id: str) -> int:
        today = self._get_beijing_today_key()
        counts = self._load_minimal_selfie_daily_counts()
        return int(counts.get(today, {}).get(str(group_id or "").strip(), 0) or 0)

    def _is_minimal_selfie_group_limit_reached(self, group_id: str) -> bool:
        rule = self._get_minimal_selfie_group_rule(group_id)
        if not rule:
            return False
        limit = int(rule.get("daily_limit", 0) or 0)
        if limit <= 0:
            return False
        return self._get_minimal_selfie_group_usage(group_id) >= limit

    def _record_minimal_selfie_group_success(self, group_id: str) -> None:
        gid = str(group_id or "").strip()
        if not gid:
            return
        today = self._get_beijing_today_key()
        counts = self._load_minimal_selfie_daily_counts()
        bucket = counts.setdefault(today, {})
        bucket[gid] = int(bucket.get(gid, 0) or 0) + 1
        self._save_minimal_selfie_daily_counts(counts)

    async def _try_reserve_minimal_selfie_group_quota(self, group_id: str) -> bool:
        rule = self._get_minimal_selfie_group_rule(group_id)
        if not rule:
            return True
        limit = int(rule.get("daily_limit", 0) or 0)
        if limit <= 0:
            return True
        async with self._minimal_selfie_quota_lock:
            if self._is_minimal_selfie_group_limit_reached(group_id):
                return False
            self._record_minimal_selfie_group_success(group_id)
            return True

    async def _release_minimal_selfie_group_quota(self, group_id: str) -> None:
        rule = self._get_minimal_selfie_group_rule(group_id)
        if not rule or int(rule.get("daily_limit", 0) or 0) <= 0:
            return
        gid = str(group_id or "").strip()
        if not gid:
            return
        async with self._minimal_selfie_quota_lock:
            today = self._get_beijing_today_key()
            counts = self._load_minimal_selfie_daily_counts()
            bucket = counts.get(today, {})
            current = int(bucket.get(gid, 0) or 0)
            if current <= 1:
                bucket.pop(gid, None)
            else:
                bucket[gid] = current - 1
            if bucket:
                counts[today] = bucket
            else:
                counts.pop(today, None)
            self._save_minimal_selfie_daily_counts(counts)

    def _get_minimal_selfie_limit_reject_prompt(self, group_id: str) -> str:
        rule = self._get_minimal_selfie_group_rule(group_id)
        if rule:
            prompt = str(rule.get("limit_reject_prompt", "") or "").strip()
            if prompt:
                return prompt
        return (
            "\u4eca\u5929\u5df2\u7ecf\u4e0d\u9002\u5408\u518d\u53d1\u81ea\u62cd\u4e86\u3002\u8bf7\u6839\u636e\u5f53\u524d\u7fa4\u804a\u8bed\u5883\uff0c\u81ea\u7136\u5730\u627e\u4e2a\u7406\u7531\u5a49\u62d2\u3002"
            "\u53ea\u56de\u590d\u4e00\u5c0f\u6bb5\u4e2d\u6587\u804a\u5929\u6d88\u606f\uff0c\u4e0d\u8981\u63d0\u989d\u5ea6\u3001\u914d\u7f6e\u3001\u9650\u5236\u6216\u7cfb\u7edf\u89c4\u5219\u3002"
        )

    async def _generate_minimal_selfie_limit_reply(
        self, group_id: str, user_message: str
    ) -> str:
        provider = getattr(self.context, "get_using_provider", lambda: None)()
        prompt = self._get_minimal_selfie_limit_reject_prompt(group_id)
        if provider is not None and hasattr(provider, "text_chat"):
            try:
                response = await provider.text_chat(
                    prompt=(
                        f"\u7fa4\u53cb\u521a\u521a\u8bf4\uff1a{str(user_message or '').strip()}\n\n"
                        "\u8bf7\u76f4\u63a5\u56de\u590d\u4e00\u6761\u7b80\u77ed\u81ea\u7136\u7684\u4e2d\u6587\u804a\u5929\u6d88\u606f\u3002"
                    ),
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt=prompt,
                )
                text = str(getattr(response, "completion_text", "") or "").strip()
                if text:
                    return text
            except Exception as exc:
                logger.warning("[PureSelfie] limit reply llm failed: %s", exc)
        return "\u4eca\u5929\u5148\u4e0d\u53d1\u81ea\u62cd\u5566\uff0c\u665a\u70b9\u518d\u6765\u627e\u6211\u5427\u3002"

    async def _judge_minimal_selfie_request(self, message_text: str) -> tuple[bool, str]:
        text = str(message_text or "").strip()
        if not text:
            return False, ""
        provider = getattr(self.context, "get_using_provider", lambda: None)()
        if provider is not None and hasattr(provider, "text_chat"):
            try:
                response = await provider.text_chat(
                    prompt=(
                        "\u5224\u65ad\u8fd9\u6761\u7fa4\u804a\u6d88\u606f\u662f\u4e0d\u662f\u5728\u8981\u6c42\u673a\u5668\u4eba\u751f\u6210\u4e00\u5f20\u81ea\u62cd\u56fe\u3002"
                        "\u5982\u679c\u662f\uff0c\u8bf7\u8865\u6210\u7b80\u6d01\u7684\u82f1\u6587\u6216\u4e2d\u82f1\u6df7\u5408\u56fe\u50cf\u63d0\u793a\u8bcd\u3002"
                        '\u53ea\u8f93\u51fa JSON\uff1a{"generate": true/false, "prompt": "..."}\u3002\n\n'
                        f"\u7528\u6237\u6d88\u606f\uff1a{text}"
                    ),
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt="You classify selfie image intent and output JSON only.",
                )
                raw = str(getattr(response, "completion_text", "") or "").strip()
                parsed = self._parse_selfie_judge_result(raw)
                if parsed[0]:
                    return parsed
            except Exception as exc:
                logger.warning(
                    "[PureSelfie] judge failed, fallback to keyword rule: %s",
                    exc,
                )
        lowered = text.lower()
        keywords = ("\u81ea\u62cd", "\u7167\u7247", "\u6765\u4e00\u5f20", "\u62cd\u4e00\u5f20", "selfie")
        if any(token in text or token in lowered for token in keywords):
            return True, text
        return False, ""

    def _get_minimal_selfie_backend(self) -> OpenAICompatBackend:
        if self.imgr is None:
            raise RuntimeError("image manager is not initialized")
        if self._minimal_selfie_backend is not None:
            return self._minimal_selfie_backend
        conf = self._get_minimal_selfie_config()
        self._minimal_selfie_backend = OpenAICompatBackend(
            imgr=self.imgr,
            base_url=conf["api_base_url"],
            api_keys=[conf["api_token"]] if conf["api_token"] else [],
            timeout=120,
            max_retries=1,
            default_model=conf["model"],
            default_size=self._resolve_minimal_selfie_size_arg(conf),
            supports_edit=True,
        )
        return self._minimal_selfie_backend

    def _get_minimal_selfie_backends(self) -> list[Any]:
        if self.imgr is None:
            raise RuntimeError("image manager is not initialized")
        conf = self._get_minimal_selfie_config()
        if self._minimal_selfie_chat_backend is None:
            self._minimal_selfie_chat_backend = OpenAIChatImageBackend(
                imgr=self.imgr,
                base_url=conf["api_base_url"],
                api_keys=[conf["api_token"]] if conf["api_token"] else [],
                timeout=120,
                max_retries=1,
                default_model=conf["model"],
                supports_edit=True,
                edit_request_mode="stream",
            )
        if self._is_google_official_openai_base_url(conf["api_base_url"]):
            return [self._minimal_selfie_chat_backend]
        compat_backend = self._get_minimal_selfie_backend()
        return [compat_backend, self._minimal_selfie_chat_backend]

    async def _generate_minimal_selfie(self, prompt: str) -> Path:
        conf = self._get_minimal_selfie_config()
        if not conf["api_base_url"]:
            raise RuntimeError("\u672a\u914d\u7f6e API \u5730\u5740")
        if not conf["model"]:
            raise RuntimeError("\u672a\u914d\u7f6e\u6a21\u578b")
        if not conf["api_token"]:
            raise RuntimeError("\u672a\u914d\u7f6e\u4ee4\u724c")

        size_arg = self._resolve_minimal_selfie_size_arg(conf)
        images = self._load_minimal_selfie_reference_file_bytes()

        backends = self._get_minimal_selfie_backends()
        chat_backend = backends[-1]

        last_error: Exception | None = None
        fallback_backends = [chat_backend] + [
            backend for backend in backends if backend is not chat_backend
        ]
        for backend in fallback_backends:
            try:
                return await backend.edit(
                    prompt,
                    images,
                    model=conf["model"],
                    size=size_arg,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[PureSelfie] backend %s failed, trying next backend: %s",
                    backend.__class__.__name__,
                    exc,
                )
        raise last_error or RuntimeError("\u6ca1\u6709\u53ef\u7528\u7684\u751f\u56fe\u540e\u7aef")

    async def _begin_user_job(self, user_id: str) -> bool:
        uid = str(user_id or "").strip()
        if not uid:
            return True
        async with self._concurrency_lock:
            count = self._image_inflight.get(uid, 0)
            if count >= 1:
                return False
            self._image_inflight[uid] = count + 1
        return True

    async def _end_user_job(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        async with self._concurrency_lock:
            count = self._image_inflight.get(uid, 0)
            if count <= 1:
                self._image_inflight.pop(uid, None)
            else:
                self._image_inflight[uid] = count - 1

    async def _send_image_with_fallback(
        self, event: AstrMessageEvent, image_path: Path
    ) -> SendImageResult:
        path = Path(image_path)
        last_error = ""
        try:
            await event.send(event.chain_result([Image.fromFileSystem(str(path))]))
            return SendImageResult(ok=True)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[PureSelfie] image send failed, fallback to file: %s", exc)
        try:
            if path.exists() and path.stat().st_size <= self.IMAGE_AS_FILE_THRESHOLD_BYTES:
                await event.send(event.chain_result([File.fromFileSystem(str(path))]))
                return SendImageResult(ok=True, reason="file_fallback")
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[PureSelfie] file fallback send failed: %s", exc)
        return SendImageResult(ok=False, reason="send_failed", last_error=last_error)

    @staticmethod
    def _summarize_status_text(
        value: Exception | str | None,
        *,
        fallback: str,
        limit: int = 180,
    ) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            return fallback
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3].rstrip()}..."

    @filter.regex(r".+", priority=-100)
    async def minimal_selfie_group_message(self, event: AstrMessageEvent):
        if not self._is_minimal_selfie_mode():
            return
        if getattr(event, "is_private_chat", lambda: False)():
            return

        group_id = str(getattr(event, "get_group_id", lambda: "")() or "").strip()
        if not self._is_group_enabled_for_minimal_selfie(group_id):
            return

        message_text = str(getattr(event, "message_str", "") or "").strip()
        if not message_text or message_text.startswith(("/", "!", "\uff1f", ".", "\u3002", "\uff0c")):
            return

        # ignore_keywords check: skip entirely if message contains any keyword
        conf = self._get_minimal_selfie_config()
        ignore_kws = [kw.lower() for kw in conf.get("ignore_keywords", []) if kw.strip()]
        if ignore_kws:
            msg_lower = message_text.lower()
            if any(kw in msg_lower for kw in ignore_kws):
                return  # completely skip, don't stop_event, let other handlers process

        should_generate, llm_prompt = await self._judge_minimal_selfie_request(message_text)
        if not should_generate:
            return

        if not await self._try_reserve_minimal_selfie_group_quota(group_id):
            reject_text = await self._generate_minimal_selfie_limit_reply(
                group_id, message_text
            )
            yield event.plain_result(reject_text)
            event.stop_event()
            return

        user_id = str(event.get_sender_id() or "").strip()
        if not await self._begin_user_job(user_id):
            await self._release_minimal_selfie_group_quota(group_id)
            yield event.plain_result("\u4f60\u5f53\u524d\u8fd8\u6709\u4e00\u5f20\u81ea\u62cd\u4efb\u52a1\u5728\u5904\u7406\u4e2d\uff0c\u7a0d\u7b49\u4e00\u4e0b\u518d\u53d1\u3002")
            event.stop_event()
            return

        try:
            await mark_processing(event)
            prompt = self._build_minimal_selfie_prompt(llm_prompt)
            image_path = await self._generate_minimal_selfie(prompt)
            sent = await self._send_image_with_fallback(event, image_path)
            if sent:
                await mark_success(event)
            else:
                await self._release_minimal_selfie_group_quota(group_id)
                await mark_failed(event)
                yield event.plain_result(
                    f"\u56fe\u7247\u53d1\u9001\u5931\u8d25\uff1a{sent.reason or sent.last_error or 'unknown'}"
                )
        except Exception as exc:
            await self._release_minimal_selfie_group_quota(group_id)
            await mark_failed(event)
            yield event.plain_result(
                f"\u81ea\u62cd\u751f\u6210\u5931\u8d25\uff1a{self._summarize_status_text(exc, fallback='unknown error')}"
            )
        finally:
            await self._end_user_job(user_id)
            event.stop_event()
