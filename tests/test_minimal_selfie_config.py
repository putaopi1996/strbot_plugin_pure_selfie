import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "minimal_selfie_only_testpkg"
CORE_PACKAGE_NAME = f"{PACKAGE_NAME}.core"
MAIN_MODULE_NAME = f"{PACKAGE_NAME}.main"


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _StubBackend:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def edit(self, *args, **kwargs):
        return Path("/tmp/stub.jpg")

    async def close(self):
        return None


class _StubImageManager:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def close(self):
        return None


class _DummyMessageComponent:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def fromFileSystem(path: str):
        return _DummyMessageComponent(path=path)


class _DummyStar:
    def __init__(self, context):
        self.context = context


class _DummyStarTools:
    @staticmethod
    def get_data_dir(name: str):
        return Path("/tmp") / name


class _DummyFilter:
    def __getattr__(self, name):
        def decorator_factory(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        return decorator_factory


class _StubRefsManager:
    """Stub that returns preset bytes for testing."""

    def __init__(self, refs_dir=None):
        self.refs_dir = refs_dir or Path("/tmp/stub-refs")
        self._files: list[Path] = []
        self._bytes: list[bytes] = [b"ref-image-a", b"ref-image-b"]

    def sync(self, config_entries):
        from dataclasses import dataclass

        @dataclass
        class _SyncResult:
            persisted: int = 0
            skipped: int = 0
            orphans_removed: int = 0
            errors: int = 0
            total_files: int = 2
            total_bytes: int = 22

        return _SyncResult()

    def list_reference_files(self) -> list[Path]:
        if self._files:
            return self._files
        return [Path("/tmp/stub-refs/a.png"), Path("/tmp/stub-refs/b.png")]

    def load_reference_bytes(self) -> list[bytes]:
        return self._bytes


def _clear_modules():
    for name in list(sys.modules):
        if name.startswith(PACKAGE_NAME) or name in {
            "astrbot",
            "astrbot.api",
            "astrbot.api.event",
            "astrbot.api.message_components",
            "astrbot.api.star",
        }:
            sys.modules.pop(name, None)


def _install_stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _load_module():
    _clear_modules()

    pkg = types.ModuleType(PACKAGE_NAME)
    pkg.__path__ = [str(ROOT)]
    sys.modules[PACKAGE_NAME] = pkg

    core_pkg = types.ModuleType(CORE_PACKAGE_NAME)
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules[CORE_PACKAGE_NAME] = core_pkg

    astrbot_mod = types.ModuleType("astrbot")
    sys.modules["astrbot"] = astrbot_mod

    api_mod = types.ModuleType("astrbot.api")
    api_mod.logger = _Logger()
    sys.modules["astrbot.api"] = api_mod

    _install_stub_module(
        "astrbot.api.event",
        AstrMessageEvent=type("AstrMessageEvent", (), {}),
        filter=_DummyFilter(),
    )
    _install_stub_module(
        "astrbot.api.message_components",
        File=_DummyMessageComponent,
        Image=_DummyMessageComponent,
    )
    _install_stub_module(
        "astrbot.api.star",
        Context=type("Context", (), {}),
        Star=_DummyStar,
        StarTools=_DummyStarTools,
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.emoji_feedback",
        mark_failed=lambda *args, **kwargs: _async_noop(),
        mark_processing=lambda *args, **kwargs: _async_noop(),
        mark_success=lambda *args, **kwargs: _async_noop(),
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.image_manager",
        ImageManager=_StubImageManager,
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.openai_chat_image_backend",
        OpenAIChatImageBackend=_StubBackend,
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.openai_compat_backend",
        OpenAICompatBackend=_StubBackend,
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.utils",
        close_session=_async_noop_fn,
        download_image=_async_download_stub,
    )
    _install_stub_module(
        f"{CORE_PACKAGE_NAME}.uploaded_refs",
        UploadedRefsManager=_StubRefsManager,
    )

    spec = importlib.util.spec_from_file_location(MAIN_MODULE_NAME, ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MAIN_MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


async def _async_noop():
    return None


async def _async_noop_fn(*args, **kwargs):
    return None


async def _async_download_stub(url):
    return b"stub-image"


async def _download_should_not_run(url):
    raise AssertionError(f"download should not run: {url}")


class MinimalSelfieConfigTests(unittest.TestCase):
    def test_runtime_config_reads_expected_fields(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "enabled_groups": ["10001", "10002"],
                    "preset_prompt": "cinematic selfie",
                    "api_base_url": "https://api.example.com/v1",
                    "model": "gpt-image-1",
                    "api_token": "token-123",
                    "ignore_keywords": ["画图", " 生成 ", "", "  "],
                    "group_rules": [
                        {
                            "group_id": "10001",
                            "daily_limit": 3,
                            "limit_reject_prompt": "today is enough",
                        }
                    ],
                }
            },
        )

        conf = plugin._get_minimal_selfie_config()

        self.assertTrue(conf["enabled"])
        self.assertEqual(conf["enabled_groups"], ["10001", "10002"])
        self.assertEqual(conf["preset_prompt"], "cinematic selfie")
        self.assertEqual(conf["api_base_url"], "https://api.example.com/v1")
        self.assertEqual(conf["model"], "gpt-image-1")
        self.assertEqual(conf["api_token"], "token-123")
        self.assertEqual(conf["image_size"], "auto")
        self.assertEqual(conf["ignore_keywords"], ["画图", "生成"])
        self.assertNotIn("reference_input_mode", conf)
        self.assertNotIn("reference_image_urls", conf)
        self.assertNotIn("reference_image_files", conf)
        self.assertNotIn("reference_image_dir", conf)
        self.assertNotIn("resolved_reference_images", conf)

    def test_google_openai_base_url_is_normalized(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={},
        )

        self.assertEqual(
            plugin._normalize_minimal_selfie_api_base_url(
                "https://generativelanguage.googleapis.com/v1beta"
            ),
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )
        self.assertEqual(
            plugin._normalize_minimal_selfie_api_base_url(
                "https://generativelanguage.googleapis.com/v1beta/openai/"
            ),
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )

    def test_group_rule_lookup_and_prompt(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "group_rules": [
                        {
                            "group_id": "12345",
                            "daily_limit": 2,
                            "limit_reject_prompt": "今天别发了",
                        }
                    ],
                }
            },
        )

        self.assertEqual(
            plugin._get_minimal_selfie_group_rule("12345"),
            {
                "group_id": "12345",
                "daily_limit": 2,
                "limit_reject_prompt": "今天别发了",
            },
        )
        self.assertEqual(
            plugin._get_minimal_selfie_limit_reject_prompt("12345"),
            "今天别发了",
        )

    def test_build_prompt_includes_preset_dynamic_and_refs(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "preset_prompt": "realistic phone selfie",
                }
            },
        )
        # Ensure _refs_manager is set with stub that has 2 files
        plugin._refs_manager = _StubRefsManager()

        prompt = plugin._build_minimal_selfie_prompt("grey hoodie, elevator mirror")

        self.assertIn("realistic phone selfie", prompt)
        self.assertIn("grey hoodie, elevator mirror", prompt)
        self.assertIn("2 files", prompt)
        self.assertIn("attached separately", prompt)
        # URLs should NOT appear in the prompt
        self.assertNotIn("https://", prompt)


class MinimalSelfieRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_minimal_selfie_uses_local_reference_bytes(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "api_base_url": "https://generativelanguage.googleapis.com/v1beta",
                    "model": "gemini-3.1-flash-image-preview",
                    "api_token": "token-123",
                    "image_size": "1024x1024",
                }
            },
        )
        await plugin.initialize()

        # Inject stub refs manager with known bytes
        stub_refs = _StubRefsManager()
        stub_refs._bytes = [b"file-a", b"file-b"]
        plugin._refs_manager = stub_refs

        class _ChatFileBackend:
            async def edit(self, prompt, images, **kwargs):
                self.prompt = prompt
                self.images = images
                self.kwargs = kwargs
                return Path("/tmp/chat-file-success.jpg")

        chat_backend = _ChatFileBackend()
        plugin._minimal_selfie_chat_backend = chat_backend

        result = await plugin._generate_minimal_selfie("mirror selfie")

        self.assertEqual(result, Path("/tmp/chat-file-success.jpg"))
        self.assertEqual(chat_backend.images, [b"file-a", b"file-b"])
        self.assertNotIn("input_image_urls", chat_backend.kwargs)

    async def test_generate_minimal_selfie_auto_size_omits_size_argument(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "api_base_url": "https://api.example.com/v1",
                    "model": "nano-banana",
                    "api_token": "token-123",
                    "image_size": "auto",
                }
            },
        )
        await plugin.initialize()

        # Inject stub refs manager with known bytes
        stub_refs = _StubRefsManager()
        stub_refs._bytes = [b"ref-img"]
        plugin._refs_manager = stub_refs

        class _ChatBackend:
            async def edit(self, prompt, images, **kwargs):
                self.kwargs = kwargs
                return Path("/tmp/chat-auto-size.jpg")

        chat_backend = _ChatBackend()
        plugin._minimal_selfie_chat_backend = chat_backend

        result = await plugin._generate_minimal_selfie("mirror selfie")

        self.assertEqual(result, Path("/tmp/chat-auto-size.jpg"))
        self.assertIsNone(chat_backend.kwargs["size"])

    async def test_generate_minimal_selfie_falls_back_to_chat_backend(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "api_base_url": "https://api.example.com/v1/images/generations",
                    "model": "nano-banana",
                    "api_token": "token-123",
                    "image_size": "1024x1024",
                }
            },
        )
        await plugin.initialize()

        # Inject stub refs manager with known bytes
        stub_refs = _StubRefsManager()
        stub_refs._bytes = [b"ref-img"]
        plugin._refs_manager = stub_refs

        class _CompatFailBackend:
            async def edit(self, *args, **kwargs):
                raise RuntimeError("404 from images endpoint")

        class _ChatSuccessBackend:
            async def edit(self, *args, **kwargs):
                return Path("/tmp/chat-success.jpg")

        plugin._get_minimal_selfie_backends = lambda: [
            _CompatFailBackend(),
            _ChatSuccessBackend(),
        ]

        result = await plugin._generate_minimal_selfie("mirror selfie")

        self.assertEqual(result, Path("/tmp/chat-success.jpg"))

    async def test_generate_minimal_selfie_raises_when_no_refs(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "api_base_url": "https://api.example.com/v1",
                    "model": "nano-banana",
                    "api_token": "token-123",
                    "image_size": "1024x1024",
                }
            },
        )
        await plugin.initialize()

        # Inject stub refs manager with NO bytes
        stub_refs = _StubRefsManager()
        stub_refs._bytes = []
        plugin._refs_manager = stub_refs

        with self.assertRaises(RuntimeError):
            await plugin._generate_minimal_selfie("mirror selfie")

    async def test_group_daily_limit_uses_beijing_date_bucket(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "group_rules": [
                        {
                            "group_id": "12345",
                            "daily_limit": 2,
                            "limit_reject_prompt": "quota reached",
                        }
                    ],
                }
            },
        )
        plugin.data_dir = Path.cwd() / ".tmp-minimal-selfie-tests"
        plugin.data_dir.mkdir(parents=True, exist_ok=True)
        plugin._get_beijing_today_key = lambda: "2026-04-30"
        counter_path = plugin._get_minimal_selfie_daily_counter_path()
        if counter_path.exists():
            counter_path.unlink()

        self.assertFalse(plugin._is_minimal_selfie_group_limit_reached("12345"))
        plugin._record_minimal_selfie_group_success("12345")
        self.assertFalse(plugin._is_minimal_selfie_group_limit_reached("12345"))
        plugin._record_minimal_selfie_group_success("12345")
        self.assertTrue(plugin._is_minimal_selfie_group_limit_reached("12345"))

        stored = json.loads(counter_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["2026-04-30"]["12345"], 2)

    async def test_group_quota_reservation_releases_on_failure(self):
        mod = _load_module()
        plugin = mod.GiteeAIImagePlugin(
            context=types.SimpleNamespace(),
            config={
                "minimal_selfie": {
                    "enabled": True,
                    "group_rules": [
                        {
                            "group_id": "12345",
                            "daily_limit": 1,
                            "limit_reject_prompt": "quota reached",
                        }
                    ],
                }
            },
        )
        plugin.data_dir = Path.cwd() / ".tmp-minimal-selfie-tests"
        plugin.data_dir.mkdir(parents=True, exist_ok=True)
        plugin._get_beijing_today_key = lambda: "2026-04-30"
        counter_path = plugin._get_minimal_selfie_daily_counter_path()
        if counter_path.exists():
            counter_path.unlink()

        self.assertTrue(await plugin._try_reserve_minimal_selfie_group_quota("12345"))
        self.assertFalse(await plugin._try_reserve_minimal_selfie_group_quota("12345"))

        await plugin._release_minimal_selfie_group_quota("12345")

        self.assertTrue(await plugin._try_reserve_minimal_selfie_group_quota("12345"))


if __name__ == "__main__":
    unittest.main()
