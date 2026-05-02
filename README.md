# AstrBot Group Selfie Only

这个仓库现在只保留一个能力：在指定群聊里监听自然语言消息，先交给当前 LLM 判断是不是在要“机器人发自拍图”，如果是，就自动补全提示词，再结合你配置的参考图生成自拍。

## 保留能力

- 指定群聊生效
- 群友直接自然语言触发，不需要命令
- LLM 负责判断是否触发，并补全本次自拍提示词
- 支持多张参考图，视为同一身份
- 每群单独配置每日次数和超限提示词
- 支持普通 OpenAI 兼容接口
- 支持 Google 官方 Gemini OpenAI 兼容接口

## WebUI 配置

只需要配置 `minimal_selfie`：

- `enabled`
- `enabled_groups`
- `preset_prompt`
- `reference_input_mode`
- `reference_image_urls`
- `reference_image_files`
- `api_base_url`
- `model`
- `api_token`
- `image_size`
- `group_rules`

最小示例：

```json
{
  "minimal_selfie": {
    "enabled": true,
    "enabled_groups": ["123456789"],
    "preset_prompt": "realistic phone selfie, natural skin texture, candid composition, single person, no collage, no text overlay, no watermark",
    "reference_input_mode": "url",
    "reference_image_urls": [
      "https://example.com/ref-1.jpg",
      "https://example.com/ref-2.jpg"
    ],
    "reference_image_files": [],
    "api_base_url": "https://api.example.com/v1",
    "model": "gpt-image-1",
    "api_token": "sk-xxxx",
    "image_size": "1024x1024",
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

## Google 官方 Gemini 配置

如果你用的是 Google 官方 Gemini OpenAI 兼容接口，建议这样配：

- `api_base_url`: `https://generativelanguage.googleapis.com/v1beta`
- `reference_input_mode`: `local_file`
- `reference_image_files`: 填插件运行机器上的图片绝对路径

插件会自动把 Google 官方地址规范成 `.../v1beta/openai`。
Google 官方这条兼容链路不适合直接传远程参考图 URL，所以插件会强制改走本地参考图文件字节上传。

示例：

```json
{
  "minimal_selfie": {
    "enabled": true,
    "enabled_groups": ["123456789"],
    "preset_prompt": "realistic phone selfie, natural skin texture, candid composition",
    "reference_input_mode": "local_file",
    "reference_image_urls": [],
    "reference_image_files": [
      "E:/selfie_refs/ref-1.png",
      "E:/selfie_refs/ref-2.png"
    ],
    "api_base_url": "https://generativelanguage.googleapis.com/v1beta",
    "model": "gemini-3.1-flash-image-preview",
    "api_token": "AIza...",
    "image_size": "1024x1024",
    "group_rules": []
  }
}
```

## 触发方式

插件只在 `enabled_groups` 里的群聊工作。群里所有自然语言消息都会先交给当前 LLM 判断，只有判断成“要生成自拍图”才会继续触发。

如果某个群在 `group_rules` 里配置了 `daily_limit`，超过次数后插件不会再请求生图接口，而是把这个群自己的 `limit_reject_prompt` 交给当前文本 LLM，让它生成一条自然婉拒消息。次数按北京时间 `00:00` 重置。

## 兼容说明

- 这是一个彻底精简版，不再保留旧的文生图、改图、批量、视频和工具链。
- 普通 OpenAI 兼容服务优先尝试 `chat/completions + image_url`，失败后回退下载参考图再上传。
- Google 官方 Gemini OpenAI 兼容接口会直接使用本地参考图文件，不走远程 URL 直传。

## 测试

```bash
pytest tests/test_minimal_selfie_config.py -q
pytest tests/test_openai_chat_stream_refs.py -q
```
