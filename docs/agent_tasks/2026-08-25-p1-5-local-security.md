# P1.5 本地安全边界修复任务

## 背景

P1.3d 与 P1.4 已分别收紧跨进程恢复和 AI / FFmpeg 失败边界。本轮只处理本地单用户 V1 的高收益安全问题，不引入登录系统或企业级权限架构。

## 目标

1. AI 配置页面和 `/api/settings/ai` 不再把已保存的 API Key、Access Key、管理员 Token 或 `.env` 绝对路径送到浏览器。
2. AI 配置输入拒绝 CR/LF/NUL、过长值、非法协议和非法 URL，阻断 `.env` 配置注入。
3. 删除已确认的动态 `innerHTML` 注入点，服务端数据只通过 `textContent` 写入 DOM。
4. 本地管理页面、API 与媒体入口默认只接受本机 Host；非本机 API 客户端必须提供 `LOCAL_ADMIN_TOKEN`。Docker 正式端口仅绑定 `127.0.0.1`。

## 允许修改范围

- `app/main.py`
- `app/models/settings.py`
- `app/services/ai_config_service.py`
- `app/templates/base.html`
- `app/templates/system_status.html`
- `app/static/js/app.js`
- `app/static/js/publish-center.js`
- `app/templates/publish.html`
- `app/services/publish_domain.py`
- `app/services/publish_scheduler.py`
- `app/services/publish_service.py`
- `docker-compose.yml`
- 安全回归测试、Codemap 与项目进度文档

## 禁止修改范围

- 不改任务、AI、字幕、切片、排期、发布的业务状态机。
- 不读取或输出 `.env` 中的真实值。
- 不访问真实 AI、平台、浏览器 Worker 或活动数据库。
- 不引入多用户、OAuth 登录、反向代理或新的第三方依赖。

## 已确定实现要求

- Secret 字段对外值固定为空，另用布尔状态表示“已配置”；空输入继续保留旧 Secret。
- Token 比较使用常量时间比较；不得把 Token 放在 HTML、JavaScript、日志或错误信息中。
- 本机 Host 支持 `localhost`、`127.0.0.1`、`::1`；测试客户端单独兼容。跨站 Origin 的写请求拒绝。
- `/health`、静态资源和 favicon 保持公开；其余非本机入口关闭，远程 API 仅允许 Bearer Token。
- 所有动态 UI 文本使用 DOM 节点与 `textContent`。
- Provider / Worker 返回的作品链接仅在 HTTP(S) 且域名与目标平台匹配时进入已发布结果或页面链接；异常链接进入人工复核。
- `.env` 写入前由 Pydantic 校验字符串边界，不做自动“修复”。

## 验收标准

- 配置 GET、保存响应和系统状态 HTML 均不包含测试 Secret 或真实 `.env` 路径。
- Secret 输入为密码框且空值表示保留现有配置。
- 换行注入、`javascript:` / `file:` URL、非法响应路径返回 422，且没有写入配置文件。
- 本机页面/API 可用；非本机 Host 无 Token 返回 403；错误 Token 返回 401；正确 Token 可访问 API；跨站写请求返回 403。
- 已确认的两处 XSS payload 只显示为文本，不进入 `innerHTML`。
- Docker 端口映射为 `127.0.0.1:8001:8001`。

## 测试命令

- `python -m ruff check app tests`
- `python -m compileall -q app`
- `node --check app/static/js/app.js`
- `node --check app/static/js/publish-center.js`
- P1.5 定向 pytest
- 使用 pytest 隔离数据库运行完整非真实外部调用测试集

## 返回格式

- 返回准确命令、隔离数据库路径、通过/失败数量。
- 明确说明没有读取 Secret、没有触发真实 AI / FFmpeg / 平台调用、没有写活动数据库。
