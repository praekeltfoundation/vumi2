FROM ghcr.io/praekeltfoundation/python-base-nw:3.11-bullseye AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src src/
RUN uv sync --locked --no-dev --no-editable --compile-bytecode

FROM ghcr.io/praekeltfoundation/python-base-nw:3.11-bullseye
COPY --from=build .venv/ .venv/

ENTRYPOINT [ "tini", "--", ".venv/bin/vumi2" ]
