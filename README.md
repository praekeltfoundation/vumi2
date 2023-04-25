# Vumi2
[Vumi](https://vumi.readthedocs.io/), but with Python 3 and Trio async

Currently under development.
- [x] To address router
- [x] HTTP RPC USSD transport
- [ ] SMPP SMS transport

## Development
This project uses [poetry](https://python-poetry.org/docs/#installation) for packaging and dependancy management, so install that first.

Ensure you're also running at least python 3.7, `python --version`.

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
~ black . && mypy --install-types . && ruff check .
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
1. In settings, set the "Python > Formatting: Provider" to "black"
1. If you want to have formatting automatically apply, in settings, check the "Editor: Format On Save" checkbox

Alternatively, add the following to your `settings.json`:
```json
{
    "python.linting.mypyEnabled": true,
    "python.formatting.provider": "black",
    "editor.formatOnSave": true,
}
```

### Editors with LSP support

If you're using an editor that supports [LSP](https://microsoft.github.io/language-server-protocol/), you can install the optional `lsp` dependency group:
```bash
~ poetry install --with lsp
```

Configuring LSP depends on your editor. In most cases, the default configuration for the relevant plugin or extension should be suitable.
