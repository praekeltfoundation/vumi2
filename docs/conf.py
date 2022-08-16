from datetime import datetime
from typing import List

import pkg_resources
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
release = pkg_resources.get_distribution(project).version
parsed_version = parse_version(release)
if isinstance(parsed_version, Version):
    version = f"{parsed_version.major}.{parsed_version.minor}"
else:
    version = release

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions: List[str] = ["sphinx.ext.autodoc"]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
html_static_path = ["_static"]
