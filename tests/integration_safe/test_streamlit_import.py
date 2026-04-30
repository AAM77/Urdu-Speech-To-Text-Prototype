"""Smoke test: the Streamlit app module + main pipeline functions are importable."""

from __future__ import annotations


def test_streamlit_app_module_imports():
    # The module itself calls main() at import time; we just need to make sure
    # the import does not raise. To avoid actually running the UI, we patch
    # streamlit before importing.
    import importlib
    import sys
    import types

    fake_st = types.ModuleType("streamlit")

    def _noop(*a, **kw):
        return None

    class _Tabs:
        def __init__(self, n: int):
            self._n = n

        def __iter__(self):
            return iter(_Ctx() for _ in range(self._n))

        def __len__(self):
            return self._n

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Status(_Ctx):
        def update(self, *a, **kw):
            return None

    class _SessionState(dict):
        def setdefault(self, k, v):  # type: ignore[override]
            return dict.setdefault(self, k, v)

    fake_st.set_page_config = _noop
    fake_st.session_state = _SessionState()
    fake_st.title = _noop
    fake_st.caption = _noop
    fake_st.header = _noop
    fake_st.write = _noop
    fake_st.subheader = _noop
    fake_st.success = _noop
    fake_st.info = _noop
    fake_st.warning = _noop
    fake_st.error = _noop
    fake_st.json = _noop
    fake_st.dataframe = _noop
    fake_st.tabs = lambda labels: [_Ctx() for _ in labels]
    fake_st.file_uploader = lambda *a, **kw: None
    fake_st.text_input = lambda *a, **kw: ""
    fake_st.number_input = lambda *a, **kw: kw.get("value", 0)
    fake_st.checkbox = lambda *a, **kw: False
    fake_st.button = lambda *a, **kw: False
    fake_st.download_button = _noop
    fake_st.status = lambda *a, **kw: _Status()

    sys.modules["streamlit"] = fake_st

    # Now import the module: it executes main() once.
    importlib.import_module("urdu_pipeline.ui.streamlit_app")


def test_main_pipeline_functions_are_importable():
    from urdu_pipeline.stages import (
        run_article_stage,
        run_chunker_stage,
        run_reconciler_stage,
        run_transcriber_stage,
        run_translator_stage,
    )

    for fn in (
        run_article_stage,
        run_chunker_stage,
        run_reconciler_stage,
        run_transcriber_stage,
        run_translator_stage,
    ):
        assert callable(fn)
