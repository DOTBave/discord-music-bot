FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DENO=/usr/local/bin/deno \
    FFMPEG_PATH=ffmpeg \
    HOME=/home/app

# 系统依赖：ffmpeg 用于音频，curl/unzip 用于安装固定版本 Deno
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 固定版本 Deno（yt-dlp 的 JS 挑战需要）：按目标架构下载 release 并校验 sha256
ARG DENO_VERSION=2.9.8
ARG TARGETARCH
RUN set -eux; \
    arch="$TARGETARCH"; \
    if [ -z "$arch" ]; then arch="$(uname -m)"; fi; \
    case "$arch" in \
        amd64|x86_64) deno_arch="x86_64-unknown-linux-gnu" ;; \
        arm64|aarch64) deno_arch="aarch64-unknown-linux-gnu" ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/denoland/deno/releases/download/v$DENO_VERSION/deno-$deno_arch.zip"; \
    curl -fsSL -o /tmp/deno.zip "$base"; \
    curl -fsSL -o /tmp/deno.zip.sha256sum "$base.sha256sum"; \
    expected="$(cut -d' ' -f1 /tmp/deno.zip.sha256sum)"; \
    actual="$(sha256sum /tmp/deno.zip | cut -d' ' -f1)"; \
    [ "$expected" = "$actual" ]; \
    unzip -q /tmp/deno.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/deno; \
    rm -f /tmp/deno.zip /tmp/deno.zip.sha256sum; \
    deno --version

# 非 root 用户运行
RUN useradd -m -u 1000 app
WORKDIR /app

# 依赖使用 uv 生成的跨平台精确锁文件（requirements.txt 仅作为抽象依赖来源）
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN chown -R app:app /app

USER app

CMD ["python", "main.py"]
