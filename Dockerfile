# The API only -- forensics is a pure Python package, so no separate build
# stage is needed here (unlike the frontend, which does need one for its JS
# toolchain). See frontend/Dockerfile for that half of the stack.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Traces and the eval-case log live outside the image so a container restart
# doesn't lose them -- see the volume mount in docker-compose.yml.
ENV FF_TRACE_DIR=/data/traces
ENV FF_EVAL_FILE=/data/eval_cases.jsonl
ENV FF_LLM_MODE=mock
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "forensics.api:app", "--host", "0.0.0.0", "--port", "8000"]
