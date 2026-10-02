FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.8.3
COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --no-editable && useradd --create-home --uid 10001 platform
USER platform
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["python", "-m", "agenticiot.serve", "--host", "0.0.0.0", "--port", "8000"]
