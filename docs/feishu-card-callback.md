# 飞书卡片回调配置指南

> 适用场景：诊断卡片已经能发到飞书群，但点击「批准执行」「拒绝」「转人工」时，飞书提示“该应用尚未配置卡片回调，一键完成配置后即可使用”。

## 1. 为什么会出现这个弹窗？

飞书交互卡片按钮不是普通链接。用户点击按钮后，飞书服务器需要把按钮动作回调到本项目的 Agent API。

当前项目的卡片审批回调接口是：

```text
POST /api/v1/approvals/callback
```

本地完整地址是：

```text
http://localhost:8000/api/v1/approvals/callback
```

但飞书云端无法访问你的 `localhost`，所以需要先给本地 Agent 暴露一个公网 HTTPS 地址。

## 2. 前置检查

先确认本地 Agent 正常运行：

```bash
./ops.sh status
```

至少需要看到：

```text
agent 正常 (http://localhost:8000/health)
```

也可以直接检查：

```bash
curl -s http://localhost:8000/health
```

预期返回：

```json
{"status":"ok"}
```

## 3. 生成公网 HTTPS 回调地址

任选一种方式即可。

### 方式 A：cloudflared

```bash
cloudflared tunnel --url http://localhost:8000
```

命令输出里会出现一个类似这样的地址：

```text
https://xxxx.trycloudflare.com
```

最终填给飞书的回调地址是：

```text
https://xxxx.trycloudflare.com/api/v1/approvals/callback
```

### 方式 B：ngrok

```bash
ngrok http 8000
```

命令输出里会出现一个类似这样的地址：

```text
https://xxxx.ngrok-free.app
```

最终填给飞书的回调地址是：

```text
https://xxxx.ngrok-free.app/api/v1/approvals/callback
```

## 4. 在飞书开放平台配置卡片回调

1. 打开 [飞书开放平台](https://open.feishu.cn/)。
2. 进入你的企业自建应用。
3. 找到 **消息卡片** / **卡片配置** / **卡片回调** 相关页面。
4. 点击弹窗里的「立即配置」，或手动进入卡片回调配置。
5. 回调地址填写：

```text
https://你的公网域名/api/v1/approvals/callback
```

6. 回调类型选择：

```text
卡片回传交互
```

如果控制台展示的是旧版名称，则选择：

```text
消息卡片回传交互（旧）
```

7. 本地联调阶段不要开启加密回调。

当前项目支持飞书地址校验和卡片按钮回调，但暂未实现加密 payload 解密。生产环境接入时再补充验签、解密和防重放校验。

## 5. 验证地址校验是否通过

飞书保存配置时会向回调地址发送一次 `challenge` 校验。你也可以先手动测：

```bash
curl -X POST https://你的公网域名/api/v1/approvals/callback \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ping"}'
```

预期返回：

```json
{"challenge":"ping"}
```

如果这个命令失败，飞书保存配置时也会失败。

## 6. 验证按钮点击

配置保存成功后，回到飞书群，重新触发一次诊断卡片，点击：

- 「批准执行」
- 「拒绝」
- 「转人工」

点击后可通过 Incident API 查看审批状态：

```bash
curl -s http://localhost:8000/api/v1/incidents/你的事件ID/approval
```

批准后的预期结果类似：

```json
{
  "incident_id": "INC-xxxx",
  "status": "approved",
  "approval_status": "approved"
}
```

也可以打开 Web Console 查看：

```text
http://localhost:8000/
```

## 7. 常见问题

### Q1：飞书提示回调地址不可用

优先检查三点：

- 回调地址必须是公网可访问的 `https://` 地址。
- 隧道工具必须保持运行。
- 本地 Agent 必须运行在 `http://localhost:8000`。

可以用下面命令排查：

```bash
curl -s http://localhost:8000/health
curl -X POST https://你的公网域名/api/v1/approvals/callback \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ping"}'
```

### Q2：飞书保存通过了，但点击按钮没有更新状态

查看 Agent 日志：

```bash
./ops.sh logs agent
```

重点搜索：

```text
审批回调
飞书卡片动作
审批状态已更新
```

如果日志里没有任何回调记录，说明飞书请求没有打到本地 Agent，通常是公网隧道断开或回调地址填错。

### Q3：是否需要配置飞书事件订阅？

按钮审批走的是“卡片回调”，不是 Alertmanager Webhook，也不是普通事件订阅。

你仍然需要配置飞书应用凭证、Bot 权限和群聊 `chat_id`，否则卡片发不到群里；但按钮点击弹窗对应的是卡片回调配置。

### Q4：我可以直接填 `localhost` 吗？

不可以。`localhost` 对飞书服务器来说是飞书自己的机器，不是你的电脑。

本地开发必须用 cloudflared、ngrok 或类似工具生成公网 HTTPS 地址。

## 8. 项目当前支持的回调格式

当前接口兼容两种飞书卡片回调：

- 旧版：`type = card_action`
- 新版：`header.event_type = card.action.trigger`

按钮值需要包含：

```json
{
  "action": "approve",
  "incident_id": "INC-xxxx"
}
```

其中 `action` 可选值：

| action | 含义 | 落库状态 |
|--------|------|----------|
| `approve` | 批准执行 | `approved` |
| `reject` | 拒绝 | `rejected` |
| `escalate` | 转人工 | `escalated` |

## 9. 相关接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/approvals/callback` | `POST` | 飞书卡片按钮回调 |
| `/api/v1/incidents/{incident_id}/approval` | `GET` | 查询审批状态 |
| `/api/v1/incidents/{incident_id}` | `GET` | 查询事件详情、Runbook、风险评估 |

官方参考：[飞书卡片回调通信方式](https://open.feishu.cn/document/feishu-cards/card-callback-communication)。
