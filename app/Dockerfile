# Festive Sale Gadget Advisor: LangGraph + OpenAI Agents SDK + MCP servers + browser-use, served with Gradio.
FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_NO_CACHE=1

# Google Chrome (read by the browser-use price agents), Xvfb (so Chrome can run headed, which marketplaces block less),
# and Node.js 22 (Tavily and Memory MCP servers).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg xvfb xauth procps fonts-liberation fonts-noto-color-emoji \
 && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y --no-install-recommends ./google-chrome-stable_current_amd64.deb \
 && rm google-chrome-stable_current_amd64.deb \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# MCP servers, pre-installed so no run has to download them.
RUN npm install -g tavily-mcp@0.2.22 @modelcontextprotocol/server-memory@2026.8.31 && npm cache clean --force
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_DOWNLOADS=never
RUN uv tool install --python /usr/local/bin/python3 yt-mcp-server==0.1.2 \
 && uv tool install --python /usr/local/bin/python3 reddit-rss-mcp==1.2.1

# browser-use lives in its own venv: it pins openai 2.x, while openai-agents needs openai 3.x.
RUN uv venv --python /usr/local/bin/python3 /opt/price-env \
 && uv pip install --python /opt/price-env/bin/python browser-use==0.13.10 python-dotenv

COPY requirements.txt /tmp/requirements.txt
RUN uv pip install --system -r /tmp/requirements.txt

# Hugging Face runs the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PRICE_WORKER_PYTHON=/opt/price-env/bin/python \
    PRICE_CHROME_PATH=/usr/bin/google-chrome-stable \
    PRICE_NO_SANDBOX=1 \
    PRICE_HEADLESS=0 \
    PRICE_CONCURRENCY=2
WORKDIR /home/user/app
COPY --chown=user . /home/user/app

EXPOSE 7860
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "python", "app.py"]
