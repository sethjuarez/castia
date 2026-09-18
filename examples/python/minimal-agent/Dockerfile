# Optional container alternative; azure.yaml defaults to remote code build.
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml requirements.txt main.py ./
COPY .agent_configs .agent_configs
RUN python -m pip install --no-cache-dir -r requirements.txt
EXPOSE 8088
CMD ["python", "main.py"]
