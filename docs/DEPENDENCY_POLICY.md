# 依赖维护策略

牛马片场使用 Python 3.12。为了避免同一份代码在不同时间安装出不同的直接依赖组合，运行时和开发依赖采用固定版本，并由 GitHub Actions 验证。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `requirements.in` | 人工维护的兼容版本范围，用于判断允许升级到哪个区间 |
| `requirements.txt` | 当前经过 CI 验证的运行时直接依赖版本 |
| `requirements-dev.txt` | 运行时依赖加 Ruff、pytest 等开发工具 |

`requirements.txt` 固定的是项目直接依赖，不是跨平台的完整 `pip freeze`。这样可以避免把 Linux 专用的间接依赖强制安装到 Windows。

## 普通用户安装

```powershell
pip install -r requirements.txt
pip check
```

## 开发环境安装

```powershell
pip install -r requirements-dev.txt
pip check
```

## 升级依赖流程

1. 查看 Dependabot PR 或在 `requirements.in` 中调整允许范围。
2. 只升级少量相关依赖，避免一次混入大量不相关变化。
3. 将验证后的直接版本写入 `requirements.txt` 或 `requirements-dev.txt`。
4. 运行：

```powershell
pip install -r requirements-dev.txt
pip check
ruff check app tests scripts/seed_demo_data.py
pytest -v
```

5. 验证三套 Compose：

```powershell
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.demo.yml config --quiet
```

6. 确认 GitHub Actions 的源码检查和 Docker image smoke test 都通过后再合并。

## 特别关注的依赖

- `playwright`：可能受 Chrome 和平台页面变化影响。
- `fastapi` / `pydantic`：升级时重点检查请求模型和页面接口。
- `faster-whisper`：体积较大，并可能受 CUDA、CTranslate2 和系统环境影响。
- `uvicorn`：升级时验证正式启动、开发热重载和健康检查。

## 安全原则

- 不为追求“最新”而自动合并依赖升级。
- Dependabot 只负责提出 PR，必须经过 CI 和人工复核。
- 依赖升级不得触发真实平台投稿测试。
- 发现安全漏洞时优先升级受影响依赖，并记录在 `CHANGELOG.md`。
