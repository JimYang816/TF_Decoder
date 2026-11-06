import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from tf_decoder import Application, AnalysisParameters
from test_application import raw_file


def test_batch_retains_paths_snapshot_and_continues_failures(tmp_path, qapp):
    source = tmp_path / '输入'
    source.mkdir()
    raw_file(source, [0], name='1.raw')
    (source / '2.raw').write_bytes(b'')
    nested = source / '子目录'
    nested.mkdir()
    raw_file(nested, [2097152], name='1.RAW')
    with (nested / '1.RAW').open('ab') as stream:
        stream.write(b'tail')
    output = tmp_path / '输出'
    output.mkdir()
    (output / '1.png').write_bytes(b'keep')
    with Application() as app:
        app.open_folder(source).result(timeout=10)
        app.apply_parameters(AnalysisParameters(), displayed_frequency_range=(1000, 12000)).result()
        def progress(state):
            if state.completed == 1 and state.status == 'exporting':
                app.apply_parameters(AnalysisParameters(sample_rate=48000), displayed_frequency_range=(0, 20000)).result(timeout=10)
                app.open_file(source / '1.raw').result(timeout=10)
        result = app.export_batch_png(output, progress=progress).result(timeout=20)
        assert result.status == 'completed'
        assert result.summary == {'success': 1, 'warning': 1, 'failed': 1, 'uncompleted': 0}
        assert [e.status for e in result.entries] == ['success', 'failed', 'warning']
        assert result.entries[0].output == output / '1_1.png'
        assert result.entries[2].output == output / '子目录' / '1.png'
        assert str(source / '2.raw') in result.entries[1].message
        assert '残缺尾块' in result.entries[2].message
        assert (output / '1.png').read_bytes() == b'keep'
        for entry in (result.entries[0], result.entries[2]):
            with Image.open(entry.output) as image:
                image.load()
                assert image.info['Frequency range (Hz)'] == '1000, 12000'
                assert 'sample_rate=96000' in image.info['Analysis parameters']
                assert image.info['Time range (s)'] == '0, 0.08533333333333333'


def test_cancel_at_public_progress_retains_completed_and_marks_remaining(tmp_path, qapp):
    for number in range(3):
        raw_file(tmp_path, [0], name=f'{number}.raw')
    with Application() as app:
        app.open_folder(tmp_path).result(timeout=10)
        def progress(state):
            if state.completed == 1:
                app.cancel_batch_export()
        result = app.export_batch_png(tmp_path / 'output', progress=progress).result(timeout=20)
        assert result.status == 'cancelled'
        assert result.summary == {'success': 1, 'warning': 0, 'failed': 0, 'uncompleted': 2}
        assert [e.status for e in result.entries] == ['success', 'uncompleted', 'uncompleted']
        assert list((tmp_path / 'output').glob('*.png')) == [result.entries[0].output]
        assert app.batch_export_status == result


def test_directory_failures_are_per_file_and_recover(tmp_path, qapp):
    raw_file(tmp_path, [0], name='1.raw')
    nested = tmp_path / 'nested'
    nested.mkdir()
    raw_file(nested, [0], name='2.raw')
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'nested').write_text('blocking directory')
    with Application() as app:
        app.open_folder(tmp_path).result(timeout=10)
        result = app.export_batch_png(output).result(timeout=20)
        assert [e.status for e in result.entries] == ['success', 'failed']
        assert str(output / 'nested') in result.entries[1].message
        assert result.entries[1].output is None
        assert (output / 'nested').read_text() == 'blocking directory'
        blocked = tmp_path / 'blocked'
        blocked.write_text('keep')
        failed = app.export_batch_png(blocked).result(timeout=20)
        assert failed.summary['failed'] == 2
        assert all(str(blocked) in e.message for e in failed.entries)


def test_cancel_processing_before_write_leaves_no_partial_image(tmp_path, qapp):
    raw_file(tmp_path, [0])
    with Application() as app:
        app.open_folder(tmp_path).result(timeout=10)
        def progress(state):
            if any(e.status == 'processing' for e in state.entries):
                app.cancel_batch_export()
        output = tmp_path / 'output'
        result = app.export_batch_png(output, progress=progress).result(timeout=10)
        assert result.summary['uncompleted'] == 1
        assert result.entries[0].output is None
        assert not output.exists()


def test_missing_source_continues_to_later_file_and_empty_batch_is_clear(tmp_path, qapp):
    missing = raw_file(tmp_path, [0], name='1.raw')
    raw_file(tmp_path, [0], name='2.raw')
    with Application() as app:
        empty = app.export_batch_png(tmp_path / 'output').result(timeout=10)
        assert empty.status == 'failed'
        assert '没有' in empty.message
        app.open_folder(tmp_path).result(timeout=10)
        missing.unlink()
        result = app.export_batch_png(tmp_path / 'output').result(timeout=20)
        assert [e.status for e in result.entries] == ['failed', 'success']
        assert str(missing) in result.entries[0].message
        with Image.open(result.entries[1].output) as image:
            image.verify()
