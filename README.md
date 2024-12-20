# Vumi2
[Vumi](https://vumi.readthedocs.io/), but with Python 3 and Trio async

Currently under development.
- [x] To address router
- [x] HTTP RPC USSD transport
- [ ] SMPP SMS transport

## Development
This project uses [poetry](https://python-poetry.org/docs/#installation) for packaging and dependancy management, so install that first.

Ensure you're also running at least python 3.11, `python --version`.

Then you can install the dependencies
```bash
~ poetry install
```

You will also need an AMQP broker (eg. [RabbitMQ](https://www.rabbitmq.com/)) installed and running to be able to run a local worker, or to run the local tests.

To run a local worker, there is the `vumi2` command. First make sure you're in the virtual environment where the project is installed
```bash
~ poetry shell
```

Then you can run a local worker using the `vumi2` command, eg.
```bash
~ vumi2 worker vumi2.routers.ToAddressRouter
```

To run the autoformatting and linting, run
```bash
~ ruff format && ruff check && mypy --install-types
```

For the test runner, we use [pytest](https://docs.pytest.org/):
```bash
~ pytest
```

## Generating documentation
This project uses [sphinx](https://www.sphinx-doc.org/) to generate the documentation. To build, run
```bash
~ cd docs
~ make html
```
The built documentation will be in `docs/_build/html`

## Editor configuration

If you'd like your editor to handle linting and/or formatting for you, here's how to set it up.

### Visual Studio Code

1. Install the Python and Ruff extensions
1. In settings, check the "Python > Linting: Mypy Enabled" box
1. In settings, set the "Python > Formatting: Provider" to "black" (apparently "ruff format" isn't supported by the Python extension yet and "black" is probably close enough)
1. If you want to have formatting automatically apply, in settings, check the "Editor: Format On Save" checkbox

Alternatively, add the following to your `settings.json`:
```json
{
    "python.linting.mypyEnabled": true,
    "python.formatting.provider": "black",
    "editor.formatOnSave": true,
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
