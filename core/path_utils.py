import os
import sys


def get_base_path():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.abspath(".")


def resource_path(relative_path: str):
    return os.path.join(get_base_path(), relative_path)


def project_path(relative_path: str):
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, relative_path)
    return os.path.abspath(relative_path)
