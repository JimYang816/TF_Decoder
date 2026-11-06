from tf_decoder import Application, Viewport
from test_application import raw_file


def test_folder_browsing_natural_paths_and_independent_viewports(tmp_path):
    for directory in ('中文2', '中文10'):
        (tmp_path / directory).mkdir()
    raw_file(tmp_path / '中文2', [0], '同名.RaW')
    raw_file(tmp_path / '中文10', [1], '同名.RAW')
    raw_file(tmp_path, [2], 'record10.raw')
    raw_file(tmp_path, [3], 'record2.raw')
    (tmp_path / 'ignored.txt').write_text('no')
    with Application() as app:
        assert app.open_folder(tmp_path).result(10).status == 'ready'
        assert [e.relative_path.as_posix() for e in app.browser.entries] == [
            'record2.raw', 'record10.raw', '中文2/同名.RaW', '中文10/同名.RAW']
        assert app.browser.current_index == 0
        view = Viewport((0.01, 0.03), (1000, 9000))
        app.set_viewport(view)
        assert app.next_file().result(10).viewport == Viewport((0, 8192/96000), (0, 48000))
        assert app.previous_file().result(10).viewport == view
        assert app.previous_file().result(10) is app.state
        app.select_file(3).result(10)
        assert app.next_file().result(10) is app.state


def test_folder_errors_summary_recovery_and_stale_requests(tmp_path):
    raw_file(tmp_path, [0], '1.raw', tail=b'broken')
    (tmp_path / '2.raw').write_bytes(b'')
    missing = raw_file(tmp_path, [0], '3.raw')
    valid = raw_file(tmp_path, [4], '4.raw')
    with Application() as app:
        assert app.open_folder(tmp_path).result(10).result.warnings
        assert app.browser.entries[1].status == 'pending'
        assert app.select_file(1).result(10).status == 'failed'
        missing.unlink()
        assert str(missing) in app.select_file(2).result(10).message
        old = app.select_file(0)
        newest = app.select_file(3)
        old.result(10)
        assert app.state.path == valid
        state = newest.result(10)
        assert app.state is state and state.path == valid
        assert app.browser.summary == {'pending': 0, 'computing': 0, 'success': 1, 'warning': 1, 'failed': 2}
        assert app.browser.entries[1].message
        # Reimport starts a fresh batch, while the single-file entry still works.
        app.set_viewport(Viewport((0.01, 0.02), (1000, 2000)))
        assert app.open_file(valid).result(10).viewport.time_range == (0, 8192/96000)
        assert len(app.browser.entries) == 1


def test_unreadable_subfolder_is_reported_without_hiding_readable_files(tmp_path, monkeypatch):
    import os
    valid = raw_file(tmp_path, [0])
    denied = tmp_path / '不可读取'
    denied.mkdir()
    scandir = os.scandir

    def read_directory(path):
        if str(path) == str(denied):
            raise PermissionError(f'{denied}: access denied')
        return scandir(path)

    monkeypatch.setattr(os, 'scandir', read_directory)
    with Application() as app:
        assert app.open_folder(tmp_path).result(10).path == valid
        assert len(app.browser.entries) == 1
        assert str(denied) in app.browser.message


def test_empty_missing_and_replaced_folder_requests(tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    with Application() as app:
        assert app.open_folder(empty).result(10).status == 'empty'
        assert app.browser.entries == () and app.browser.current_index is None
        assert app.open_folder(tmp_path / 'missing').result(10).status == 'failed'
        valid = raw_file(tmp_path, [0])
        first = app.open_folder(tmp_path)
        last = app.open_file(valid)
        first.result(10)
        final = last.result(10)
        assert app.state is final
        assert len(app.browser.entries) == 1
