FROM ghcr.io/praekeltfoundation/python-base-nw:3.10-bullseye as build

RUN pip install poetry==1.4.2
COPY . ./
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-dev --no-interaction --no-ansi

FROM ghcr.io/praekeltfoundation/python-base-nw:3.10-bullseye
COPY --from=build .venv/ .venv/
COPY src src/

ENTRYPOINT [ "tini", "--", ".venv/bin/vumi2" ]
