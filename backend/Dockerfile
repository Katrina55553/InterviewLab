FROM python:3.12-slim

WORKDIR /app

# 换华为云镜像源（加速 apt）
RUN sed -i 's|deb.debian.org|repo.huaweicloud.com|g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libpq-dev \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --require-hashes \
    --only-binary=:all: \
    -i https://repo.huaweicloud.com/repository/pypi/simple/ \
    --default-timeout=300 \
    --retries=5 \
    -r requirements.txt

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
