"""Gauge store wiring for gauge_registry.py.

gauge_registry.py tries:
    from stream_conditions.storage.gauges import gauge_store

This module intentionally does not export gauge_store yet — the try/except
in gauge_registry.py catches the ImportError and falls back to InMemoryGaugeStore.

When the app starts up (e.g. in cli.py or app.py), call:
    from stream_conditions.sources.gauge_registry import set_default_store
    set_default_store(my_store)
to wire in persistent storage.
"""
