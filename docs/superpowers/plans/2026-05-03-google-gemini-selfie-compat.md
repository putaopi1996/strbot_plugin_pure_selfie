# Google Gemini Selfie Compat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让最小自拍插件兼容 Google 官方 Gemini OpenAI 兼容接口，并支持本地参考图文件作为输入。

**Architecture:** 保持现有最小自拍插件入口不变，在配置层增加参考图输入模式和本地文件列表；对 Google 官方地址自动规范成 `/openai`，并在生成链路中强制走本地参考图文件字节上传，不再使用远程 URL 直传。

**Tech Stack:** Python, AstrBot plugin runtime, OpenAI-compatible backend wrappers, pytest

---

### Task 1: 扩展配置解析

**Files:**
- Modify: `main.py`
- Modify: `_conf_schema.json`
- Test: `tests/test_minimal_selfie_config.py`

- [x] **Step 1: 写失败测试**
- [x] **Step 2: 跑测试确认失败**
- [x] **Step 3: 给 `minimal_selfie` 增加 `reference_input_mode` 和 `reference_image_files`**
- [x] **Step 4: 规范 Google 官方 `api_base_url`**
- [x] **Step 5: 重新跑测试确认通过**

### Task 2: 接入 Google 官方本地参考图链路

**Files:**
- Modify: `main.py`
- Test: `tests/test_minimal_selfie_config.py`

- [x] **Step 1: 写失败测试**
- [x] **Step 2: 跑测试确认失败**
- [x] **Step 3: 实现本地参考图文件读取**
- [x] **Step 4: 对 Google 官方接口禁用远程 URL 直传，改走图片字节上传**
- [x] **Step 5: 重新跑测试确认通过**

### Task 3: 更新文档

**Files:**
- Modify: `README.md`
- Modify: `metadata.yaml`

- [x] **Step 1: 更新配置说明**
- [x] **Step 2: 增加 Google 官方配置示例**
- [x] **Step 3: 明确 Google 官方推荐使用本地参考图文件**
