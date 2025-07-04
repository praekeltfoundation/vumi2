import importlib.metadata
from datetime import datetime

from packaging.version import Version
from packaging.version import parse as parse_version

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "vumi2"
author = "praekelt.org"
copyright = f"{datetime.now().year}, {author}"
release = importlib.metadata.distribution(project).version
parsed_version = parse_version(release)
if isinstance(parsed_version, Version):
    version = f"{parsed_version.major}.{parsed_version.minor}"
else:
    version = release

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions: list[str] = [
    "sphinx.ext.todo",
    "sphinxcontrib.httpdomain",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

add_module_names = False

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
## If we ever need static assets, we can put them in here.
# html_static_path = ["_static"]
html_static_path: list[str] = []

# Extension configs

todo_include_todos = True
