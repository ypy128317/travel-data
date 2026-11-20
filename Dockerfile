# ============ 旅游数据平台 后端镜像 ============
# 基于 uv 官方镜像，Python 3.12
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# 先拷贝依赖清单，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装依赖到 .venv（不装项目本身）
RUN uv sync --frozen --no-install-project --no-dev

# ============ 运行阶段 ============
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 从构建阶段拷贝虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 拷贝项目源码
COPY app/ ./app/
COPY seeds/ ./seeds/
COPY generate/ ./generate/
COPY sql/ ./sql/
COPY kb_data/ ./kb_data/
COPY init_db.py ./
COPY pyproject.toml uv.lock ./

ENV KB_DATA_DIR=/app/kb_data

# 监听 0.0.0.0，让容器外（局域网/公网）能访问；端口取 .env 的 APP_PORT，默认 8000
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
