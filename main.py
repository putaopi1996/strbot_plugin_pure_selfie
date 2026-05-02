from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image
from astrbot.api.star import Context, Star, StarTools

from .core.emoji_feedback import mark_failed, mark_processing, mark_success
from .core.image_manager import ImageManager
from .core.openai_chat_image_backend import OpenAIChatImageBackend
from .core.openai_compat_backend import OpenAICompatBackend
from .core.utils import close_session, download_image


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
        self._concurrency_lock = asyncio.Lock()
        self._image_inflight: dict[str, int] = {}
        self._minimal_selfie_backend: OpenAICompatBackend | None = None
        self._minimal_selfie_chat_backend: OpenAIChatImageBackend | None = None
        self._minimal_selfie_quota_lock = asyncio.Lock()

    async def initialize(self):
        self.imgr = ImageManager(self.config, self.data_dir)
        conf = self._get_minimal_selfie_config()
        logger.info(
            "[GroupSelfieOnly] enabled=%s groups=%s refs=%s model=%s",
            conf["enabled"],
            conf["enabled_groups"],
            len(conf["reference_image_urls"]),
            conf["model"] or "<empty>",
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

    def _get_minimal_selfie_config(self) -> dict[str, Any]:
        raw = self.config.get("minimal_selfie") or {}
        enabled_groups = [
            str(item).strip()
            for item in raw.get("enabled_groups", []) or []
            if str(item).strip()
        ]
        reference_image_urls = [
            str(item).strip()
            for item in raw.get("reference_image_urls", []) or []
            if str(item).strip()
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
            "reference_image_urls": reference_image_urls,
            "api_base_url": str(raw.get("api_base_url", "") or "").strip(),
            "model": str(raw.get("model", "") or "").strip(),
            "api_token": str(raw.get("api_token", "") or "").strip(),
            "image_size": str(raw.get("image_size", "1024x1024") or "").strip()
            or "1024x1024",
            "group_rules": group_rules,
        }

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
        if conf["reference_image_urls"]:
            refs = "\n".join(f"- {url}" for url in conf["reference_image_urls"])
            parts.append(f"Reference image URLs:\n{refs}")
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
            "今天已经不适合再发自拍了。请根据当前群聊语境，自然地找个理由婉拒，"
            "只回复一小段中文聊天消息，不要提额度、配置、限制或系统规则。"
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
                        f"群友刚刚说：{str(user_message or '').strip()}\n\n"
                        "请直接回复一条简短自然的中文聊天消息。"
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
                logger.warning("[GroupSelfieOnly] limit reply llm failed: %s", exc)
        return "今天先不发自拍啦，晚点再来找我吧。"

    async def _judge_minimal_selfie_request(self, message_text: str) -> tuple[bool, str]:
        text = str(message_text or "").strip()
        if not text:
            return False, ""
        provider = getattr(self.context, "get_using_provider", lambda: None)()
        if provider is not None and hasattr(provider, "text_chat"):
            try:
                response = await provider.text_chat(
                    prompt=(
                        "判断这条群聊消息是不是在要求机器人生成一张自拍图。"
                        "如果是，请补成简洁的英文或中英混合图像提示词。"
                        '只输出 JSON：{"generate": true/false, "prompt": "..."}。\n\n'
                        f"用户消息：{text}"
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
                logger.warning("[GroupSelfieOnly] judge failed, fallback to keyword rule: %s", exc)
        lowered = text.lower()
        keywords = ("自拍", "自画像", "照片", "来一张", "拍一张", "selfie")
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
            default_size=conf["image_size"],
            supports_edit=True,
        )
        return self._minimal_selfie_backend

    def _get_minimal_selfie_backends(self) -> list[Any]:
        if self.imgr is None:
            raise RuntimeError("image manager is not initialized")
        conf = self._get_minimal_selfie_config()
        compat_backend = self._get_minimal_selfie_backend()
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
        return [compat_backend, self._minimal_selfie_chat_backend]

    async def _generate_minimal_selfie(self, prompt: str) -> Path:
        conf = self._get_minimal_selfie_config()
        if not conf["api_base_url"]:
            raise RuntimeError("未配置 API 地址")
        if not conf["model"]:
            raise RuntimeError("未配置模型")
        if not conf["api_token"]:
            raise RuntimeError("未配置令牌")
        if not conf["reference_image_urls"]:
            raise RuntimeError("未配置参考图 URL")

        backends = self._get_minimal_selfie_backends()
        chat_backend = backends[-1]
        try:
            return await chat_backend.edit(
                prompt,
                [],
                model=conf["model"],
                size=conf["image_size"],
                input_image_urls=conf["reference_image_urls"],
            )
        except Exception as exc:
            logger.warning(
                "[GroupSelfieOnly] remote URL edit via %s failed, falling back to downloaded uploads: %s",
                chat_backend.__class__.__name__,
                exc,
            )

        images: list[bytes] = []
        for url in conf["reference_image_urls"]:
            data = await download_image(url)
            if not data:
                raise RuntimeError(f"参考图下载失败: {url}")
            images.append(data)

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
                    size=conf["image_size"],
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[GroupSelfieOnly] backend %s failed, trying next backend: %s",
                    backend.__class__.__name__,
                    exc,
                )
        raise last_error or RuntimeError("没有可用的生图后端")

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
        try:
            await event.send(event.chain_result([Image.fromFileSystem(str(path))]))
            return SendImageResult(ok=True)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[GroupSelfieOnly] image send failed, fallback to file: %s", exc)
        try:
            if path.exists() and path.stat().st_size <= self.IMAGE_AS_FILE_THRESHOLD_BYTES:
                await event.send(event.chain_result([File.fromFileSystem(str(path))]))
                return SendImageResult(ok=True, reason="file_fallback")
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[GroupSelfieOnly] file fallback send failed: %s", exc)
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
        if not message_text or message_text.startswith(("/", "!", "！", ".", "。", "．")):
            return

        should_generate, llm_prompt = await self._judge_minimal_selfie_request(message_text)
        if not should_generate:
            return

        if not await self._try_reserve_minimal_selfie_group_quota(group_id):
            reject_text = await self._generate_minimal_selfie_limit_reply(group_id, message_text)
            yield event.plain_result(reject_text)
            event.stop_event()
            return

        user_id = str(event.get_sender_id() or "").strip()
        if not await self._begin_user_job(user_id):
            await self._release_minimal_selfie_group_quota(group_id)
            yield event.plain_result("你当前还有一张自拍任务在处理中，稍等一下再发。")
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
                    f"图片发送失败：{sent.reason or sent.last_error or 'unknown'}"
                )
        except Exception as exc:
            await self._release_minimal_selfie_group_quota(group_id)
            await mark_failed(event)
            yield event.plain_result(
                f"自拍生成失败：{self._summarize_status_text(exc, fallback='unknown error')}"
            )
        finally:
            await self._end_user_job(user_id)
            event.stop_event()
