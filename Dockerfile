FROM ghcr.io/praekeltfoundation/python-base-nw:3.10-bullseye as build

RUN pip install poetry==1.1.12
COPY . ./
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-dev --no-interaction --no-ansi

FROM ghcr.io/praekeltfoundation/python-base-nw:3.10-bullseye
COPY --from=build .venv/ .venv/

ENTRYPOINT [ "tini", "--", ".venv/bin/vumi2" ]