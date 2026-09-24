# 个人本机 Linux 运行说明

> 说明：这份说明只服务我个人在 Linux / WSL 上维护这个仓库时的本机运行环境，不属于原工程通用部署文档。

本文档说明如何在仓库目录内准备 Linux 开发环境，并快速启动 API 或主程序。

## 适用场景

- 你在 Linux 主机或 WSL 上开发本仓库
- 希望把虚拟环境放在项目目录内，而不是系统目录或临时目录
- 希望后续用一条脚本命令完成安装、自检和启动

## 推荐目录

仓库默认使用项目内虚拟环境：

```bash
.venv-linux/
```

这样做的好处是：

- 环境跟当前仓库绑定，切换项目时不容易混淆
- 不依赖 `/tmp`、系统 Python site-packages 或其他机器的路径
- 更适合排查“从 Windows 拷贝到 Linux 后运行异常”的问题

## 一次性准备

先在仓库根目录执行：

```bash
./scripts/run-local-linux.sh install
```

脚本会自动完成：

- 创建 `.venv-linux`
- 升级 `pip`、`setuptools`、`wheel`
- 安装 `requirements.txt`
- 默认设置 `LITELLM_LOCAL_MODEL_COST_MAP=true`，避免 Linux 本地启动时因 LiteLLM 远程模型价格表初始化而卡住

如果你还没准备配置文件，再复制一份：

```bash
cp .env.example .env
```

## 启动前自检

先做一次轻量检查：

```bash
./scripts/run-local-linux.sh check
```

这会在 `.venv-linux` 中执行 FastAPI import smoke，适合快速确认依赖、路由和应用初始化没有明显问题。

## 启动 API

默认监听 `127.0.0.1:8000`：

```bash
./scripts/run-local-linux.sh api
```

自定义地址和端口：

```bash
./scripts/run-local-linux.sh api 0.0.0.0 8010
```

启动成功后，可用下面方式检查：

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/auth/status
```

## 运行主程序

查看参数：

```bash
./scripts/run-local-linux.sh main --help
```

运行一次快速 dry-run：

```bash
./scripts/run-local-linux.sh main --stocks 600519 --no-market-review --dry-run
```

## 常见说明

- 若缺少 `.env`，脚本会给出提示，但不会自动生成真实配置。
- 首次启动时，部分三方依赖可能打印 warning；是否阻断以实际进程是否成功启动为准。
- `run-local-linux.sh` 默认会导出 `LITELLM_LOCAL_MODEL_COST_MAP=true`，优先使用 LiteLLM 内置本地 cost map，减少本地开发时因远程 cost map 拉取过慢导致的启动卡顿；若你明确需要恢复 LiteLLM 远程 cost map，可在执行前手动设置 `LITELLM_LOCAL_MODEL_COST_MAP=false`。
- 若你重新切到新的 Linux 机器，优先重新执行一次 `install`，不要直接复用其他机器打包出来的虚拟环境目录。

## 手动方式

如果你不想走脚本，也可以手动执行：

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
export LITELLM_LOCAL_MODEL_COST_MAP=true
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```
