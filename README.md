# Vumi2
[Vumi](https://vumi.readthedocs.io/), but with Python 3 and Trio async

Currently under development.
- [x] To address router
- [x] HTTP RPC USSD transport
- [ ] SMPP SMS transport

## Development
This project uses [uv](https://docs.astral.sh/uv/) for packaging and dependency management, so install that first.

Ensure you're also running at least python 3.11, `python --version`.

Then you can install the dependencies
```bash
~ uv sync --all-groups
```

You will also need an AMQP broker (eg. [RabbitMQ](https://www.rabbitmq.com/)) installed and running to be able to run a local worker, or to run the local tests.

To run a local worker, there is the `vumi2` command, eg.
```bash
~ uv run vumi2 worker vumi2.routers.ToAddressRouter
```

To run the autoformatting, linting, and type checking, run
```bash
~ uv run ruff format && uv run ruff check && uv run ty check src tests
```

For the test runner, we use [pytest](https://docs.pytest.org/):
```bash
~ uv run pytest
```

## Generating documentation
This project uses [sphinx](https://www.sphinx-doc.org/) to generate the documentation. To build, run
```bash
~ uv run sphinx-build -b html docs docs/_build/html
```
The built documentation will be in `docs/_build/html`

## Editor configuration

If you'd like your editor to handle linting and/or formatting for you, here's how to set it up.

### Visual Studio Code

1. Install the Python, Ruff, and ty extensions
1. In settings, set Ruff as the default Python formatter
1. If you want to have formatting automatically apply, in settings, check the "Editor: Format On Save" checkbox

Alternatively, add the following to your `settings.json`:
```json
{
    "python.defaultInterpreterPath": ".venv/bin/python",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff"
    },
    "editor.formatOnSave": true
}
```

## Release process

To release a new version, follow these steps:

1. Make sure all relevant PRs are merged and that all necessary QA testing is complete
1. Make sure release notes are up to date and accurate
1. In one commit on the `main` branch:
   - Update the version number in `pyproject.toml` to the release version
   - Replace the UNRELEASED header in `CHANGELOG.md` with the release version and date
1. Tag the release commit with the release version (for example, `v0.2.1` for version `0.2.1`)
1. Push the release commit and tag
1. In one commit on the `main` branch:
   - Update the version number in `pyproject.toml` to the next pre-release version
   - Add a new UNRELEASED header in `CHANGELOG.md`
1. Push the post-release commit
