FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install foundational packages, Git, Python, Node, Go, Build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    git \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    golang-go \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install common testing and linting tools
RUN pip3 install --no-cache-dir --break-system-packages \
    pytest \
    pytest-asyncio \
    ruff \
    mypy \
    black

# Setup unprivileged workspace directory
WORKDIR /workspace
RUN chmod -R 777 /workspace

CMD ["tail", "-f", "/dev/null"]
