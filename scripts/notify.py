"""Webhook 通知推送（零依赖，urllib 实现）。

将文本消息推送到 webhook（钉钉/企业微信/飞书机器人按 URL 自动适配
payload 格式，其余按通用 JSON POST），供每日信号/模拟盘等命令在
cron 定时任务中主动通知，替代人工盯屏。

推送失败只告警不中断主流程（通知是增值动作，不应影响信号产出）。
"""

from __future__ import annotations

import json
import sys
import urllib.request


def build_payload(url: str, text: str, title: str = "Alpha Forge") -> dict:
    """按 webhook 域名适配消息格式；未识别时用通用 {title, text}。"""
    if "oapi.dingtalk.com" in url:
        return {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    if "qyapi.weixin.qq.com" in url:
        return {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    if "open.feishu.cn" in url or "open.larksuite.com" in url:
        return {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    return {"title": title, "text": text}


def send_webhook(url: str, text: str, title: str = "Alpha Forge", timeout: float = 10.0) -> bool:
    """POST 文本消息到 webhook；成功返回 True，失败告警并返回 False。"""
    payload = build_payload(url, text, title)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True
            print(f"[warn] webhook 返回 HTTP {resp.status}，通知可能未送达", file=sys.stderr)
            return False
    except Exception as exc:
        print(f"[warn] webhook 推送失败（{type(exc).__name__}: {exc}），不影响信号输出", file=sys.stderr)
        return False
