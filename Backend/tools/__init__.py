"""
Importing this package registers every tool with Backend.tools.registry —
each submodule's @tool-decorated functions run their decorator (and thus
register themselves) as soon as the module is imported.

Backend/agent.py does `import Backend.tools` for this side effect before
calling registry.get_schemas().
"""

from Backend.tools import files, images, media, memory, reminders, system, web  # noqa: F401
