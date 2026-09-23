FROM python:3.12-slim

# 安装系统依赖与 ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Deno 用于 yt-dlp 解密
RUN curl -fsSL https://deno.land/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno

# 注入环境变量供 main.py 读取
ENV DENO=/usr/local/bin/deno
ENV FFMPEG_PATH=ffmpeg

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]