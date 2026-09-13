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
# Shell form, not exec form: hosts like Render assign the listen port via
# $PORT at runtime and expect the container to bind to it, which only a shell
# does the substitution for. Falls back to 8000 for docker-compose/local runs
# where $PORT is never set. `exec` replaces the shell with uvicorn instead of
# running it as a child, so SIGTERM on a restart/redeploy reaches uvicorn
# directly for a clean shutdown instead of the shell eating it.
CMD sh -c 'exec uvicorn forensics.api:app --host 0.0.0.0 --port ${PORT:-8000}'
