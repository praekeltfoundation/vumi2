from importlib import import_module

from vumi2.errors import InvalidWorkerClass


def _cfs_err(class_path: str, msg: str) -> InvalidWorkerClass:
    return InvalidWorkerClass(f"Error loading '{class_path}': {msg}")


def class_from_string(class_path: str):
    """
    Given a string path, return the class object.
    """
    if "." not in class_path:
        # TODO: Better output for this?
        raise _cfs_err(class_path, "Invalid worker class")
    module_path, class_name = class_path.rsplit(".", 1)
    try:
        module = import_module(module_path)
    except ModuleNotFoundError as e:
        # There's a module we can't find. There are three ways this could happen:
        #
        # * The module we're trying to import is missing. This is the common
        #   case, and we want to report it in a friendly way.
        #
        # * A parent of the module we're trying to import is missing. For
        #   example, we want `foo.bar.baz` but `foo` exists while `foo.bar`
        #   does not. This is essentially a variant of the above, and we want
        #   to report it in a similar way.
        #
        # * The module we're trying to import is there, but it's trying to
        #   import something else and that something else is missing. This
        #   should be handled like any other exception from inside the module
        #   we're importing, so we reraise.

        name = str(e.name)  # This is allowed to be None, so stringify it for mypy
        if name == module_path:
            # Exact matches are easy.
            raise _cfs_err(class_path, str(e)) from e
        if module_path.startswith(name) and module_path[len(name)] == ".":
            # e.name must be a module path prefix, not just a string prefix.
            raise _cfs_err(class_path, str(e)) from e
        else:
            # This isn't the module we're trying to import, so reraise.
            raise

    if not hasattr(module, class_name):
        raise _cfs_err(class_path, f"No class named '{class_name}'")

    return getattr(module, class_name)
