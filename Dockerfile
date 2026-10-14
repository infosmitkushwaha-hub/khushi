FROM python:3.12-slim

# Sumit's LinkedIn MCP fork (connect-note + send_message fixes). Bump the sha to take newer fixes.
ARG LINKEDIN_MCP_REF=592f63a2476aa9bcc9c24c513411b573c10c3a24

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    LINKEDIN_POSTING_MCP_PATH=/app/vendor/linkedin-posting-mcp \
    PLAYWRIGHT_BROWSERS_PATH=/opt/patchright \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    DISPLAY=:99 \
    HEADLESS=false \
    LINKEDIN_MCP_CONTAINER=true

# Node: Claude Code CLI (OAuth mode) + the posting MCP. uv: installs the LinkedIn reader. Xvfb + openbox + x11vnc +
# noVNC/websockify: the reader's headed browser and its /linkedin_login viewer. gosu: drop root. tini: reap Chromium.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git gosu tini xvfb openbox x11vnc novnc websockify \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code \
 && pip install --no-cache-dir uv \
 && rm -rf /var/lib/apt/lists/*

# LinkedIn reader MCP from the fork, with its browser pre-installed (it would otherwise download on first use)
RUN uv tool install --python 3.12 \
        "mcp-server-linkedin @ git+https://github.com/infosmitkushwaha-hub/sumit-linkdin-mcp-latest-@${LINKEDIN_MCP_REF}" \
 && "$UV_TOOL_DIR/mcp-server-linkedin/bin/patchright" install-deps chromium \
 && "$UV_TOOL_DIR/mcp-server-linkedin/bin/patchright" install chromium --no-shell \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# LinkedIn posting MCP (vendored, MIT): build once
COPY vendor/linkedin-posting-mcp /app/vendor/linkedin-posting-mcp
RUN cd /app/vendor/linkedin-posting-mcp && npm ci && npm run build && npm prune --omit=dev

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY . .
RUN useradd -m agent && mkdir -p /app/data \
 && chown -R agent:agent /app /opt/patchright && chmod +x /app/scripts/*.sh

# 6080 = noVNC for /linkedin_login only (the viewer runs only while a login is open)
EXPOSE 6080
ENTRYPOINT ["tini", "-e", "143", "--", "/app/scripts/entrypoint.sh"]
CMD ["python", "-m", "app", "serve"]
