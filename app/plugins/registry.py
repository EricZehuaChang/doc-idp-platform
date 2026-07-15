"""Plugin registry — fork-and-own copy of KBase m5-1 kbase/plugins/registry.py
(19-line pattern, design v0.2 §2 confirmed reuse list item #1).
Implementations register at import time; instantiate by (kind, name).
"""


class PluginRegistry:
    def __init__(self):
        self._plugins: dict[tuple[str, str], type] = {}

    def register(self, kind: str, name: str):
        def deco(cls):
            self._plugins[(kind, name)] = cls
            return cls
        return deco

    def create(self, kind: str, name: str, **kwargs):
        key = (kind, name)
        if key not in self._plugins:
            known = [n for k, n in self._plugins if k == kind]
            raise KeyError(f"plugin not registered: {kind}/{name}, known: {known}")
        return self._plugins[key](**kwargs)


registry = PluginRegistry()
