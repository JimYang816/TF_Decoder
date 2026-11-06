import numpy as np
from pathlib import Path
from threading import Event
from tf_decoder import Application, AnalysisParameters, Viewport
from test_application import raw_file


def test_cached_switch_preserves_result_and_stat_changes_refresh_it(tmp_path):
    first = raw_file(tmp_path, [2097152], '1.raw')
    raw_file(tmp_path, [0], '2.raw')
    with Application(cache_budget_bytes=1000000) as app:
        original = app.open_folder(tmp_path).result(10)
        app.next_file().result(10)
        returned = app.previous_file().result(10)
        assert returned.result is original.result
        raw_file(tmp_path, [0], '1.raw', tail=b'changed')
        refreshed = app.select_file(0).result(10)
        assert refreshed.path == first
        assert np.all(refreshed.result.psd == 0)
        assert refreshed.result.warnings == ('已跳过残缺尾块：7 字节',)


def test_evicted_results_keep_viewports_and_latest_applied_configuration(tmp_path):
    for index in range(6):
        raw_file(tmp_path, [2097152 if index == 0 else 0], f'{index}.raw')
    # One result is ~123 KiB; this budget deliberately forces eviction.
    with Application(cache_budget_bytes=130000) as app:
        first = app.open_folder(tmp_path).result(10)
        expected = first.result.psd.copy()
        levels = first.result.levels
        view = Viewport((.01, .03), (5000, 10000))
        app.set_viewport(view)
        for index in range(1, 6):
            assert app.select_file(index).result(10).result.voltage_range == (0, 0)
        returned = app.select_file(0).result(10)
        assert returned.viewport == view
        np.testing.assert_array_equal(returned.result.psd, expected)
        assert returned.result.levels == levels
        old = app.select_file(1)
        newest = app.select_file(0)
        changed = app.apply_parameters(AnalysisParameters(sample_rate=48000))
        old.result(10)
        newest.result(10)
        final = changed.result(10)
        assert app.state is final
        assert final.path.name == '0.raw'
        assert final.result.parameters.sample_rate == 48000
        assert final.result.frequencies[-1] == 24000
        np.testing.assert_allclose(final.result.psd, expected * 2)
        assert final.viewport != view


def test_prefetch_never_blocks_cached_view_and_newest_foreground_passes_backlog(tmp_path, monkeypatch):
    for index in range(4):
        raw_file(tmp_path, [index * 2097152], f'{index}.raw')
    # Gate the filesystem boundary so the next-file background read is observable,
    # without replacing any application collaborator or assuming compute timings.
    started, release = Event(), Event()
    original_open = Path.open
    def read(path, *args, **kwargs):
        if path == tmp_path / '1.raw' and args and args[0] == 'rb':
            started.set()
            assert release.wait(10)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', read)
    with Application() as app:
        try:
            first = app.open_folder(tmp_path).result(10)
            assert started.wait(10)  # adjacent read began without explicit navigation
            cached = app.select_file(0)
            assert cached.done()
            assert cached.result().result is first.result
            order = []
            older = app.select_file(2)
            older.add_done_callback(lambda _: order.append('older'))
            newest = app.select_file(3)
            newest.add_done_callback(lambda _: order.append('newest'))
        finally:
            release.set()
        final = newest.result(10)
        older.result(10)
        assert order == ['newest', 'older']
        assert app.state is final
        assert final.path.name == '3.raw'
        assert final.result.voltage_range == (3.0735, 3.0735)
        prefetched = app.select_file(1).result(10)
        assert prefetched.result.voltage_range == (1.0245, 1.0245)
        assert prefetched.result.levels[1] == float(prefetched.result.db.max())
