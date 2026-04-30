# AstrBot Group Selfie Only

这个仓库现在只保留一个能力：在指定群聊里监听自然语言消息，先交给当前 LLM 判断是不是在要“机器人自拍图”，如果是，就自动补全提示词，再结合你配置的参考图 URL 调用 OpenAI 兼容接口生成自拍。

## 保留能力

- 指定群聊生效
- 群友直接说自然语言，不需要命令
- LLM 负责判断是否触发，以及补全本次自拍提示词
- 多张参考图 URL 作为同一身份参考
- 每群单独配置每日次数和超限提示词
- 优先尝试 OpenAI 兼容 `images` 链路，失败时自动回退到 `chat/completions` 流式出图

## WebUI 配置

现在只需要配置 `minimal_selfie`：

- `enabled`
- `enabled_groups`
- `preset_prompt`
- `reference_image_urls`
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
    "reference_image_urls": [
      "https://example.com/ref-1.jpg",
      "https://example.com/ref-2.jpg"
    ],
    "api_base_url": "https://api.example.com/v1",
    "model": "gpt-image-1",
    "api_token": "sk-xxxx",
    "image_size": "1024x1024",
    "group_rules": [
      {
        "group_id": "123456789",
        "daily_limit": 5,
        "limit_reject_prompt": "今天已经发太多自拍了，请结合群聊气氛，自然找个理由婉拒。"
      }
    ]
  }
}
```

## 触发方式

插件只在 `enabled_groups` 里的群聊工作。群里所有自然语言消息都会先交给当前 LLM 判断，只有判断成“要生成自拍图”才会继续触发。

超过某个群在 `group_rules` 里配置的 `daily_limit` 后，插件不会再请求生图接口，而是把这个群自己的 `limit_reject_prompt` 交给当前文本 LLM，让它生成一条自然的婉拒回复。次数按北京时间 `00:00` 重置。

## 兼容说明

- 这是一个彻底精简版，不再保留文生图、改图、批量、视频、自拍参考图管理、LLM tools、多 provider 调度这些旧能力。
- 参考图会优先走 `chat/completions` 的 `image_url` 直传模式；如果目标服务商不接受远程 URL，插件才会回退到“先下载到本机，再上传给服务商”。
- 如果你的服务商真正可用的是 `.../v1/chat/completions`，插件会在 `images` 编辑链路失败后自动尝试聊天流式出图。

## 测试

```bash
pytest tests/test_minimal_selfie_config.py -q
```
