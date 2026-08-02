"""Compatibility namespace for integrations using the previous URL path."""


def __getattr__(name):
    if name == "ImportWorkspace":
        from app.pipeline.workspace import ImportWorkspace
        return ImportWorkspace
    if name == "run_url_import_pipeline":
        from app.pipeline.stages import run_url_import_pipeline
        return run_url_import_pipeline
    raise AttributeError(name)

__all__ = [
    "ImportWorkspace",
    "run_url_import_pipeline",
]
