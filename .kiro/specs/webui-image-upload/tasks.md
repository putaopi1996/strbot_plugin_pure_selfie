# Implementation Plan: WebUI Image Upload

## Overview

Replace the legacy URL/file-path reference image configuration with AstrBot WebUI native file upload (`"type": "file"`), add content-hash-based persistence in `uploaded_refs/`, synchronize on startup, and introduce `ignore_keywords` for message exclusion. Implementation uses Python 3.11+ with pytest and hypothesis for testing.

## Tasks

- [x] 1. Create `core/uploaded_refs.py` module with UploadedRefsManager
  - [x] 1.1 Create `core/uploaded_refs.py` with `SyncResult` dataclass and `UploadedRefsManager` class
    - Define `SyncResult` dataclass with fields: `persisted`, `skipped`, `orphans_removed`, `errors`, `total_files`, `total_bytes`
    - Implement `UploadedRefsManager.__init__(self, refs_dir: Path)` that creates the directory if missing
    - Implement `content_hash(self, data: bytes) -> str` using SHA-256 hex digest
    - Implement `persist_image(self, data: bytes) -> Path | None` with format validation (magic bytes for PNG/JPEG/WebP), 10 MB size check, and content-hash filename
    - Implement `sync(self, config_entries: list[dict]) -> SyncResult` that decodes base64 entries, persists new images, removes orphan files not in config
    - Implement `list_reference_files(self) -> list[Path]` returning sorted image files
    - Implement `load_reference_bytes(self) -> list[bytes]` reading all reference files
    - Use `core/image_format.py` utilities (`guess_image_mime_and_ext_strict`, `decode_base64_image_payload`) for format detection
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 5.1, 5.2, 5.3, 5.4, 7.1, 7.2, 7.3_

  - [ ]* 1.2 Write property test: Content-addressed naming (Property 1)
    - **Property 1: Content-addressed naming**
    - **Validates: Requirements 3.3, 3.4, 7.1**
    - For any valid image bytes (PNG, JPEG, or WebP, ≤ 10 MB), `persist_image` produces a file named `{sha256_hex}.{ext}` where ext matches magic bytes

  - [ ]* 1.3 Write property test: Invalid format rejection (Property 2)
    - **Property 2: Invalid format rejection**
    - **Validates: Requirements 3.5, 7.1, 7.2**
    - For any bytes without recognized PNG/JPEG/WebP magic bytes, `persist_image` returns None and writes no file

  - [ ]* 1.4 Write property test: Oversized image rejection (Property 3)
    - **Property 3: Oversized image rejection**
    - **Validates: Requirements 7.3**
    - For any bytes > 10 MB (even with valid magic), `persist_image` returns None and writes no file

  - [ ]* 1.5 Write property test: Sync correctness (Property 4)
    - **Property 4: Sync correctness**
    - **Validates: Requirements 3.1, 5.1, 5.2, 5.3**
    - After sync: every valid config entry exists on disk by hash, no orphan files remain, invalid entries produce no files

  - [ ]* 1.6 Write property test: Sync idempotence (Property 5)
    - **Property 5: Sync idempotence**
    - **Validates: Requirements 5.4**
    - Calling `sync(entries)` twice yields `persisted == 0` on second call with identical directory state

  - [ ]* 1.7 Write property test: Load returns all persisted files (Property 6)
    - **Property 6: Load returns all persisted files**
    - **Validates: Requirements 4.1**
    - For N valid persisted images, `load_reference_bytes()` returns exactly N byte arrays matching original content

- [x] 2. Modify `_conf_schema.json`: remove legacy fields, add new fields
  - [x] 2.1 Update `_conf_schema.json`
    - Remove fields: `reference_input_mode`, `reference_image_urls`, `reference_image_files`, `reference_image_dir`
    - Add `reference_images` field with `"type": "file"`, `"description": "参考人像（可多张）"`, `"hint": "建议上传清晰正脸照，可多张做不同角度参考。"`, `"file_types": ["jpg", "jpeg", "png", "webp"]`, `"default": []`
    - Add `ignore_keywords` field with `"type": "list"`, `"description": "排除关键词"`, `"hint": "消息中包含任一关键词时，本插件完全不介入（不调用 LLM 判断、不触发生图）。用于避免和其他插件冲突。"`, `"default": []`
    - _Requirements: 1.1, 2.1, 2.2, 6.1, 6.2_

  - [ ]* 2.2 Write unit tests for schema field correctness
    - Verify `_conf_schema.json` does not contain legacy fields
    - Verify `reference_images` has correct type, file_types, description, hint, default
    - Verify `ignore_keywords` has correct type, description, hint, default
    - _Requirements: 1.1, 2.1, 2.2, 6.1, 6.2_

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Integrate `UploadedRefsManager` and `ignore_keywords` into `main.py`
  - [x] 4.1 Simplify `_get_minimal_selfie_config()` and add ignore_keywords parsing
    - Remove parsing of `reference_input_mode`, `reference_image_urls`, `reference_image_files`, `reference_image_dir`, `resolved_reference_images`
    - Add parsing of `ignore_keywords` as a list of stripped strings
    - Keep existing fields: `enabled`, `enabled_groups`, `preset_prompt`, `api_base_url`, `model`, `api_token`, `image_size`, `group_rules`
    - _Requirements: 1.2, 6.3_

  - [x] 4.2 Add sync call in `initialize()` and update reference loading
    - Import `UploadedRefsManager` from `core.uploaded_refs`
    - In `initialize()`, instantiate `UploadedRefsManager` with `{data_dir}/uploaded_refs`
    - Call `sync()` with `config["minimal_selfie"]["reference_images"]` entries
    - Log `SyncResult` summary (count and total size)
    - Detect legacy fields (`reference_image_urls`, `reference_image_files`, `reference_image_dir`) and log deprecation warning once
    - Update `_load_minimal_selfie_reference_file_bytes()` to delegate to `UploadedRefsManager.load_reference_bytes()`
    - Make `_should_use_local_reference_files()` always return True
    - _Requirements: 3.1, 3.2, 4.1, 4.2, 4.3, 1.3, 7.4_

  - [x] 4.3 Add `ignore_keywords` check in message handler
    - In `minimal_selfie_group_message()`, after extracting `message_text` and before `_judge_minimal_selfie_request()`, add keyword substring match
    - If any keyword matches (case-insensitive), return immediately without stopping event
    - Skip check entirely when `ignore_keywords` is empty
    - _Requirements: 6.3, 6.4, 6.5_

  - [x] 4.4 Simplify `_build_minimal_selfie_prompt()` and `_generate_minimal_selfie()`
    - Remove URL-based reference image logic from prompt builder
    - Update prompt to always reference local attached files
    - Simplify `_generate_minimal_selfie()` to always use local bytes from `UploadedRefsManager`
    - Raise RuntimeError if no reference images available when selfie generation is triggered
    - _Requirements: 4.2, 4.4_

  - [ ]* 4.5 Write property test: Keyword exclusion (Property 7)
    - **Property 7: Keyword exclusion**
    - **Validates: Requirements 6.3**
    - For any non-empty ignore_keywords and message containing a keyword (case-insensitive substring), the handler skips without invoking LLM

  - [ ]* 4.6 Write unit tests for main.py integration
    - Test legacy field detection logs deprecation warning
    - Test `ignore_keywords` empty means no check performed
    - Test `ignore_keywords` match skips LLM call
    - Test no reference images raises RuntimeError on selfie trigger
    - Test initialization logs reference image count and size
    - _Requirements: 1.3, 4.3, 4.4, 6.4, 6.5, 7.4_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Update documentation
  - [x] 6.1 Update README.md and CHANGELOG.md
    - Add configuration instructions for `reference_images` file upload
    - Document `ignore_keywords` usage
    - Add changelog entry for v6.1.0 describing the migration from URL/path to WebUI upload
    - Note that legacy fields are no longer used
    - _Requirements: 1.1, 2.1, 6.1_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The `core/uploaded_refs.py` module is intentionally kept free of AstrBot dependencies for easy unit testing
- Existing tests in `tests/test_minimal_selfie_config.py` will need updates after main.py changes (covered in task 4.6)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2"] },
    { "id": 2, "tasks": ["1.5", "1.6", "1.7"] },
    { "id": 3, "tasks": ["4.1", "4.2"] },
    { "id": 4, "tasks": ["4.3", "4.4"] },
    { "id": 5, "tasks": ["4.5", "4.6"] },
    { "id": 6, "tasks": ["6.1"] }
  ]
}
```
