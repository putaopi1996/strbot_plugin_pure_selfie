# Requirements Document

## Introduction

本功能为 AstrBot 插件 `astrbot_plugin_gitee_aiimg` 增加通过 WebUI 配置界面直接上传参考图的能力，并移除原有的 `reference_image_urls`、`reference_image_files`、`reference_image_dir` 和 `reference_input_mode` 配置项。

上游仓库（v4.3.5）已在 `features.selfie.reference_images` 中使用 AstrBot 原生的 `"type": "file"` schema 类型实现了 WebUI 文件上传。本地精简版（v6.0.0）需要引入相同机制：在 `_conf_schema.json` 的 `minimal_selfie` 配置中新增 `reference_images` 字段（`type: file`），替换掉原来不好用的 URL/路径配置。

## Glossary

- **WebUI**: AstrBot 的 Web 管理界面，用于配置插件参数
- **Plugin**: 指 `astrbot_plugin_gitee_aiimg` 插件本身（本地精简版 v6.0.0）
- **Reference_Image**: 用于生图时保持身份一致性的参考图片
- **Data_Directory**: 插件通过 `StarTools.get_data_dir("astrbot_plugin_gitee_aiimg")` 获取的数据存储目录
- **Uploaded_Refs_Directory**: Data_Directory 下专门存放用户通过 WebUI 上传的参考图的子目录（路径为 `{Data_Directory}/uploaded_refs`）
- **Config_Schema**: 插件的 `_conf_schema.json` 文件，定义 WebUI 配置表单的结构
- **AstrBot_File_Type**: AstrBot WebUI 原生支持的 `"type": "file"` schema 字段类型，可渲染文件上传控件

## Requirements

### Requirement 1: 移除旧参考图配置项

**User Story:** As a 插件用户, I want 配置界面只保留上传方式, so that 我不会被无效的 URL 和文件路径选项所困扰。

#### Acceptance Criteria

1. THE Config_Schema SHALL NOT contain the fields `reference_image_urls`, `reference_image_files`, `reference_image_dir`, or `reference_input_mode`
2. THE Plugin SHALL NOT read or use `reference_image_urls`, `reference_image_files`, `reference_image_dir`, or `reference_input_mode` from configuration during normal operation
3. WHEN the Plugin loads a configuration that still contains legacy fields (`reference_image_urls`, `reference_image_files`, `reference_image_dir`), THE Plugin SHALL ignore those fields and log a deprecation warning once during initialization

### Requirement 2: WebUI 文件上传配置字段

**User Story:** As a 插件用户, I want 在 WebUI 配置界面中看到一个图片上传区域, so that 我可以直接从浏览器上传参考图。

#### Acceptance Criteria

1. THE Config_Schema SHALL define a field `reference_images` inside `minimal_selfie` with `"type": "file"`, `"file_types": ["jpg", "jpeg", "png", "webp"]`, and `"default": []`
2. THE Config_Schema SHALL include a description "参考人像（可多张）" and a hint "建议上传清晰正脸照，可多张做不同角度参考。" for the `reference_images` field
3. WHEN the WebUI renders the `minimal_selfie` configuration section, THE WebUI SHALL display a file upload area that accepts multiple image files of the specified types

### Requirement 3: 上传图片持久化与加载

**User Story:** As a 插件用户, I want 上传的参考图在插件重启后仍然可用, so that 我不需要每次重启后重新上传。

#### Acceptance Criteria

1. WHEN the Plugin initializes and `reference_images` contains file data provided by AstrBot WebUI, THE Plugin SHALL decode and persist each image file to the Uploaded_Refs_Directory
2. THE Plugin SHALL create the Uploaded_Refs_Directory at path `{Data_Directory}/uploaded_refs` if the directory does not exist
3. WHEN saving an uploaded image, THE Plugin SHALL generate a filename using a content hash (SHA-256 of file bytes) to prevent duplicates and ensure idempotent writes
4. WHEN saving an uploaded image, THE Plugin SHALL detect the image format from file content and use the correct file extension
5. IF an uploaded image entry is corrupted or has an unrecognizable format, THEN THE Plugin SHALL skip that entry and log a warning message

### Requirement 4: 上传图片参与生图流程

**User Story:** As a 插件用户, I want 通过 WebUI 上传的参考图能够自动参与生图流程, so that 我不需要额外配置就能使用上传的图片。

#### Acceptance Criteria

1. WHEN resolving reference images for selfie generation, THE Plugin SHALL use all valid files persisted in the Uploaded_Refs_Directory as the reference image source
2. WHEN sending reference images to any API backend, THE Plugin SHALL read the persisted files from disk and provide them as local file bytes
3. THE Plugin SHALL log the count of available reference images during initialization
4. IF no reference images are available in the Uploaded_Refs_Directory, THEN THE Plugin SHALL raise an error when a selfie generation is triggered

### Requirement 5: 上传图片同步管理

**User Story:** As a 插件用户, I want 通过 WebUI 增删参考图后配置能自动同步, so that 磁盘上不会残留已删除的图片。

#### Acceptance Criteria

1. WHEN the Plugin initializes, THE Plugin SHALL synchronize the Uploaded_Refs_Directory contents with the current `reference_images` configuration entries
2. IF the Uploaded_Refs_Directory contains files whose content hash does not match any entry in `reference_images`, THEN THE Plugin SHALL remove those orphaned files
3. WHEN a new image is added to `reference_images` that does not yet exist on disk, THE Plugin SHALL persist it to the Uploaded_Refs_Directory
4. THE Plugin SHALL use content hash comparison to determine whether an image already exists on disk, avoiding redundant writes on every restart

### Requirement 6: 关键词排除（避免插件冲突）

**User Story:** As a 插件用户, I want 配置一组排除关键词, so that 包含这些关键词的消息完全不会触发本插件的 LLM 判断和生图流程，避免与其他插件冲突。

#### Acceptance Criteria

1. THE Config_Schema SHALL define a field `ignore_keywords` inside `minimal_selfie` with `"type": "list"` and `"default": []`
2. THE Config_Schema SHALL include a description "排除关键词" and a hint "消息中包含任一关键词时，本插件完全不介入（不调用 LLM 判断、不触发生图）。用于避免和其他插件冲突。"
3. WHEN a group message contains any keyword listed in `ignore_keywords` (case-insensitive substring match), THE Plugin SHALL skip processing that message entirely without invoking LLM judgment
4. THE Plugin SHALL perform keyword matching before any LLM call or image generation logic
5. WHEN `ignore_keywords` is empty, THE Plugin SHALL not perform any keyword exclusion check

### Requirement 7: 文件格式与大小校验

**User Story:** As a 插件管理员, I want 对上传的图片进行基本校验, so that 无效或过大的文件不会影响插件正常运行。

#### Acceptance Criteria

1. WHEN validating an uploaded image, THE Plugin SHALL verify the file content matches a supported image format (PNG, JPEG, WebP) by inspecting magic bytes
2. IF an uploaded file does not match any supported image format, THEN THE Plugin SHALL skip that file, log a warning, and continue processing remaining files
3. THE Plugin SHALL reject uploaded images larger than 10 MB per file before persisting to disk
4. THE Plugin SHALL log the total count and total size of persisted reference images during initialization
