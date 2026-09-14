import json
from unittest.mock import Mock

import pytest

from meowth.core.config import TranslationConfig
from meowth.core.engine import TranslationEngine
from meowth.gui.callbacks import GUICallbacks
from meowth.gui.components.progress_view import ProgressView
from meowth.gui.eta import TranslationETA, format_duration


@pytest.fixture
def eta():
    clock = Mock(return_value=0.0)
    return TranslationETA(clock), clock


def test_eta_counts_down_and_reestimates_after_slower_batch(eta):
    estimate, clock = eta
    estimate.update(0, 100)
    assert estimate.remaining() is None
    clock.return_value = 10
    estimate.update(10, 100)
    assert estimate.remaining() == 90
    clock.return_value = 15
    assert estimate.remaining() == 85
    clock.return_value = 40
    estimate.update(20, 100)
    assert estimate.remaining() == 160


def test_eta_includes_retry_pauses_and_does_not_sum_overlaps(eta):
    estimate, clock = eta
    estimate.update(0, 100)
    clock.return_value = 10
    estimate.update(10, 100)
    estimate.add_wait(120)
    assert estimate.remaining() == 210
    clock.return_value = 20
    estimate.add_wait(30)
    assert estimate.remaining() == 200
    estimate.add_wait(120)
    assert estimate.remaining() == 210


def test_eta_overdue_is_not_completion_and_can_recover(eta):
    estimate, clock = eta
    estimate.update(0, 20)
    clock.return_value = 10
    estimate.update(10, 20)
    clock.return_value = 25
    assert estimate.remaining() is None
    estimate.update(15, 20)
    assert estimate.remaining() > 0
    estimate.update(20, 20)
    assert estimate.remaining() == 0
    estimate.reset()
    assert estimate.remaining() is None


def test_fast_glossary_burst_does_not_predict_api_speed(eta):
    estimate, clock = eta
    estimate.update(0, 1000)
    clock.return_value = 0.01
    estimate.update(800, 1000)
    assert estimate.remaining() is None
    clock.return_value = 10.01
    estimate.update(810, 1000)
    assert estimate.remaining() == pytest.approx(190)


@pytest.mark.parametrize("seconds, expected", [
    (0, "00:00:00"), (1.2, "00:00:02"), (65, "00:01:05"), (3661, "01:01:01"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("invalid_response", [False, True])
def test_progress_includes_local_tables_api_tables_and_dialogue(tmp_path, invalid_response):
    callbacks = Mock()
    translator = Mock()
    translator.translate_batch.side_effect = lambda texts, context: [
        "" if invalid_response else text.replace("Hello", "Ciao") for text in texts
    ]
    engine = TranslationEngine(
        TranslationConfig(target_lang="it", work_dir=tmp_path, batch_size=1, max_workers=2),
        callbacks=callbacks, translator=translator,
    )
    source = tmp_path / "texts.json"
    source.write_text(json.dumps({"tables": [
        {"category": "pokemon_names", "entries": [{"original": "PIKACHU"}, {"original": "EMBERCUB"}]},
        {"category": "item_descriptions", "entries": [
            {"original": "Hello curious traveler"}, {"original": r"\p"},
        ]},
    ], "free_texts": [{"original": "Hello friend"}, {"original": "Hello again"}]}), encoding="utf-8")
    engine.translate_texts(source, tmp_path / "translated.json")
    progress = [call.args for call in callbacks.on_progress.call_args_list]
    assert progress[0][1:3] == (0, 6)
    assert progress[-1][1:3] == (6, 6)
    assert all(stage == "translate" and total == 6 for stage, _, total, _ in progress)
    counts = [current for _, current, _, _ in progress]
    assert counts == sorted(set(counts))
    assert 4 in counts  # All table entries finish before dialogue processing.


def make_progress_view():
    # Exercise the real lifecycle methods without requiring a desktop display.
    view = object.__new__(ProgressView)
    view._eta = TranslationETA()
    view._timer_id = None
    view._timer_active = False
    view.after = Mock(return_value="timer")
    view.after_cancel = Mock()
    for name in ("eta_label", "batch_label", "progress_bar", "extract_icon", "extract_label",
                 "translate_icon", "translate_label", "build_icon", "build_label"):
        setattr(view, name, Mock())
    return view


@pytest.mark.parametrize("finish", ["completed", "failed", "stop", "reset"])
def test_gui_countdown_stops_and_ignores_late_events(finish):
    view = make_progress_view()
    view.set_stage("translate", "started")
    view.update("translate", 0, 100, "Preparing")
    assert view._timer_active
    view.after.assert_called_once_with(1000, view._tick_eta)
    if finish == "stop":
        view.stop_eta("Arresto in corso…")
    elif finish == "reset":
        view.reset()
    else:
        view.set_stage("translate", finish)
    view.after_cancel.assert_called_once_with("timer")
    assert not view._timer_active
    assert view._timer_id is None
    text = view.eta_label.configure.call_args
    view.add_retry_wait(120)
    view.update("translate", 50, 100, "Late event")
    view._tick_eta()
    assert view.eta_label.configure.call_args == text
    assert view.after.call_count == 1


def test_retry_callback_schedules_main_thread_update():
    app, view = Mock(), Mock()
    callbacks = GUICallbacks(app, view, Mock())
    callbacks.on_retry_wait(120)
    app.after.assert_called_once_with(0, view.add_retry_wait, 120)
