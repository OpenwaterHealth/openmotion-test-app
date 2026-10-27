"""Operator procedures that ship with the test app rather than the SDK.

Each module here runs like an ``omotion.scripts.wi15_*`` procedure: the
Procedures pane launches it as a child process (``python -m`` from source,
``--run-procedure`` in a release) and talks to it over stdin/stdout. They are
meant to move into the SDK (``omotion.scripts``) once it carries them.
"""
