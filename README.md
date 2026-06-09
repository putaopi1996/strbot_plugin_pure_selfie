# 纯纯自拍 Pure Selfie

这个仓库现在只保留一个能力：在指定群聊里监听自然语言消息，先交给当前 LLM 判断是不是在要"机器人发自拍图"，如果是，就自动补全提示词，再结合你配置的参考图生成自拍。

## 保留能力

- 指定群聊生效
- 群友直接自然语言触发，不需要命令
- LLM 负责判断是否触发，并补全本次自拍提示词
- 支持多张参考图，视为同一身份
- 每群单独配置每日次数和超限提示词
- 支持普通 OpenAI 兼容接口
- 支持 Google 官方 Gemini OpenAI 兼容接口
- 排除关键词：消息包含指定关键词时完全跳过，避免与其他插件冲突

## WebUI 配置

只需要配置 `minimal_selfie`：

- `enabled` — 启用插件
- `enabled_groups` — 生效群号列表
- `preset_prompt` — 预制提示词
- `reference_images` — 参考人像（WebUI 直接上传，支持多张 jpg/jpeg/png/webp）
- `api_base_url` — 接口地址
- `model` — 生图模型
- `api_token` — API 令牌
- `image_size` — 输出尺寸
- `ignore_keywords` — 排除关键词列表
- `group_rules` — 群级规则（每日次数、超限提示词）

### 参考图上传（reference_images）

在 WebUI 配置界面中，`reference_images` 是一个文件上传区域。直接从浏览器选择或拖拽图片即可，支持多张。

- 支持格式：jpg、jpeg、png、webp
- 单张大小限制：10 MB
- 建议上传清晰正脸照，可多张做不同角度参考
- 上传后插件会自动持久化到本地 `uploaded_refs/` 目录，重启不丢失
- 不再需要手动填写 URL 或文件路径

### 排除关键词（ignore_keywords）

配置一组关键词，当群消息中包含任一关键词时（不区分大小写），本插件完全不介入——不调用 LLM 判断、不触发生图。

适用场景：避免和群里其他插件冲突（比如其他 bot 的触发词）。

留空则不做任何排除检查。

### 最小示例

```json
{
  "minimal_selfie": {
    "enabled": true,
    "enabled_groups": ["123456789"],
    "preset_prompt": "realistic phone selfie, natural skin texture, candid composition, single person, no collage, no text overlay, no watermark",
    "reference_images": [],
    "api_base_url": "https://api.example.com/v1",
    "model": "gpt-image-1",
    "api_token": "sk-xxxx",
    "image_size": "auto",
    "ignore_keywords": ["画图", "/draw"],
    "group_rules": [
      {
        "group_id": "123456789",
        "daily_limit": 5,
        "limit_reject_prompt": "今天已经发太多自拍了，请结合群聊氛围，自然找个理由婉拒"
      }
    ]
  }
}
```

> `reference_images` 在 JSON 示例中显示为空数组——实际使用时通过 WebUI 文件上传控件添加图片，无需手动编辑此字段。

### Google 官方 Gemini 接口说明

如果你用 Google 官方 Gemini OpenAI 兼容接口，`api_base_url` 填：

```
https://generativelanguage.googleapis.com/v1beta
```

插件会自动把 Google 官方地址规范成 `.../v1beta/openai`，无需手动补全。参考图走本地字节上传，和普通 OpenAI 兼容服务行为一致。

## 触发方式

插件只在 `enabled_groups` 里的群聊工作。群里所有自然语言消息都会先交给当前 LLM 判断，只有判断成"要生成自拍图"才会继续触发。

如果消息命中了 `ignore_keywords` 中的任意关键词，则直接跳过，不做任何 LLM 调用。

如果某个群在 `group_rules` 里配置了 `daily_limit`，超过次数后插件不会再请求生图接口，而是把这个群自己的 `limit_reject_prompt` 交给当前文本 LLM，让它生成一条自然婉拒消息。次数按北京时间 `00:00` 重置。

## 兼容说明

- 这是一个彻底精简版，不再保留旧的文生图、改图、批量、视频和工具链。
- 旧的 `reference_input_mode`、`reference_image_urls`、`reference_image_files`、`reference_image_dir` 配置项已移除，不再使用。如果你的配置中仍有这些字段，插件会忽略它们并输出一次弃用警告日志。
- 所有参考图统一通过 WebUI 上传管理，插件始终使用本地字节上传到生图 API。

## 测试

```bash
pytest tests/ -q
```
