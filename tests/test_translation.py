import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from meowth.translator import DeepLQuotaExceededError, Translator
from meowth.translation_validation import TranslationValidationError, validate_translation
from meowth.glossary import Glossary
from meowth.control_codes import protect, restore
from meowth.core.config import TranslationConfig
from meowth.core.engine import TranslationEngine
from meowth.text_wrap import wrap_text


@pytest.fixture
def request_clock(monkeypatch):
    clock = Mock(now=1000.0, waits=[])

    def wait(translator, seconds):
        translator._check_cancelled()
        if seconds > 0:
            clock.waits.append(seconds)
            clock.now += seconds

    monkeypatch.setattr("meowth.translator.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("meowth.translator.random.uniform", lambda low, high: 0.0)
    monkeypatch.setattr(Translator, "_wait", wait)
    return clock


@pytest.fixture
def translator_factory(tmp_path, request_clock):
    instances = []

    def create(handler, **options):
        translator = Translator(cache_dir=tmp_path / "cache", api_key=options.pop("api_key", "test:fx"), target_lang="it", **options)
        translator._client.close()
        translator._client = httpx.Client(transport=httpx.MockTransport(handler))
        instances.append(translator)
        return translator

    yield create
    for instance in instances:
        instance.close()


def test_deepl_protocol_xml_and_cache(translator_factory):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://api-free.deepl.com/v2/translate"
        assert request.headers["Authorization"] == "DeepL-Auth-Key test:fx"
        body = json.loads(request.content)
        assert body["source_lang"] == "EN" and body["target_lang"] == "IT"
        assert body["formality"] == "prefer_less"
        assert body["ignore_tags"] == ["keep"]
        assert "<keep>{P0}</keep>" in body["text"][0]
        assert "&amp;" in body["text"][0] and "&lt;" in body["text"][0]
        return httpx.Response(200, json={"translations": [
            {"text": text.replace("Hello", "Ciao")} for text in body["text"]
        ]})

    translator = translator_factory(handle, provider="deepl")
    source = "Hello {P0} & <friend> {C0}"
    assert translator.translate_batch([source, source, "", "{C0}"]) == [
        "Ciao {P0} & <friend> {C0}", "Ciao {P0} & <friend> {C0}", "", "{C0}"
    ]
    translator.translate_batch([source])
    assert len(requests) == 1
    assert "test:fx" not in "".join(p.read_text(encoding="utf-8") for p in translator.cache_dir.iterdir())


def test_deepl_limits_and_order(translator_factory):
    sizes = []

    def handle(request):
        batch = json.loads(request.content)["text"]
        sizes.append(len(batch))
        assert len(request.content) < 128 * 1024
        return httpx.Response(200, json={"translations": [{"text": t} for t in batch]})

    translator = translator_factory(handle, provider="deepl")
    texts = [f"Text {n}" for n in range(101)]
    assert translator.translate_batch(texts) == texts
    assert sizes == [50, 50, 1]
    sizes.clear()
    large = [str(n) + "é" * 32000 for n in range(3)]
    assert translator.translate_batch(large) == large
    assert sizes == [1, 1, 1]


@pytest.mark.parametrize("status, calls", [(403, 1), (456, 1), (429, 8), (503, 8)])
def test_api_errors_not_silently_accepted(translator_factory, status, calls):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, json={"message": "failure"})

    translator = translator_factory(handle, provider="deepl")
    with pytest.raises(httpx.HTTPStatusError):
        translator.translate_batch(["Hello"])
    assert len(seen) == calls
    assert not list(translator.cache_dir.iterdir())


def test_retry_then_success(translator_factory, request_clock):
    waits = request_clock.waits

    def handle(request):
        if not waits:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"translations": [{"text": "<text>Ciao</text>"}]})

    assert translator_factory(handle, provider="deepl").translate_batch(["Hello"]) == ["Ciao"]
    assert waits == [5]


def test_deepl_multiple_keys_rotate_in_order_and_replay_same_payload(translator_factory):
    calls, logs = [], []
    keys = ["one:fx", "two:fx", "three:fx"]

    def handle(request):
        calls.append(request)
        if request.headers["Authorization"] != "DeepL-Auth-Key three:fx":
            return httpx.Response(456)
        body = json.loads(request.content)
        return httpx.Response(200, json={"translations": [
            {"text": text.replace("Hello", "Ciao")} for text in body["text"]
        ]})

    translator = translator_factory(handle, provider="deepl", api_key=" one:fx, ,two:fx,one:fx,three:fx, ",
                                    on_log=lambda *args: logs.append(args))
    assert translator.translate_batch(["Hello friend"]) == ["Ciao friend"]
    assert [r.headers["Authorization"] for r in calls] == [f"DeepL-Auth-Key {key}" for key in keys]
    assert calls[0].content == calls[1].content == calls[2].content
    assert "2/3" in logs[0][1] and "3/3" in logs[1][1]
    assert translator.translate_batch(["Hello again"]) == ["Ciao again"]
    assert calls[-1].headers["Authorization"] == "DeepL-Auth-Key three:fx"
    cache_text = "".join(path.read_text(encoding="utf-8") for path in translator.cache_dir.glob("*.json"))
    assert all(key not in str(logs) + cache_text for key in keys)


@pytest.mark.parametrize("base_url", [None, "https://custom.test/v2/"])
def test_deepl_key_switch_updates_endpoint_and_preserves_cache(translator_factory, base_url):
    calls = []

    def handle(request):
        calls.append(request)
        if request.headers["Authorization"] == "DeepL-Auth-Key free:fx":
            return httpx.Response(456)
        body = json.loads(request.content)
        return httpx.Response(200, json={"translations": [
            {"text": text.replace("Hello", "Ciao")} for text in body["text"]
        ]})

    options = dict(provider="deepl", api_key="free:fx,pro-key", base_url=base_url)
    translator = translator_factory(handle, **options)
    assert translator.translate_batch(["Hello"]) == ["Ciao"]
    assert [r.url.host for r in calls] == (["custom.test", "custom.test"] if base_url else
                                        ["api-free.deepl.com", "api.deepl.com"])
    assert translator.translate_batch(["Hello"]) == ["Ciao"]
    assert translator.translate_batch(["Hello again"]) == ["Ciao again"]
    restarted = translator_factory(Mock(side_effect=AssertionError("Cache should survive key rotation")), **options)
    assert restarted.translate_batch(["Hello", "Hello again"]) == ["Ciao", "Ciao again"]


def test_deepl_exhausted_keys_are_not_retried_by_other_workers(translator_factory):
    from concurrent.futures import ThreadPoolExecutor
    calls = []

    def handle(request):
        calls.append(request.headers["Authorization"])
        return httpx.Response(456)

    translator = translator_factory(handle, provider="deepl", api_key="first:fx,second:fx")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(translator._request_json, "translate", {}) for _ in range(4)]
        for future in futures:
            with pytest.raises(DeepLQuotaExceededError, match="Tutte le 2 chiavi"):
                future.result(timeout=2)
    assert calls == ["DeepL-Auth-Key first:fx", "DeepL-Auth-Key second:fx"]


@pytest.mark.parametrize("status, count", [(403, 1), (429, 8), (503, 8)])
def test_deepl_non_quota_errors_do_not_switch_keys(translator_factory, status, count):
    calls = []

    def handle(request):
        calls.append(request.headers["Authorization"])
        return httpx.Response(status)

    translator = translator_factory(handle, provider="deepl", api_key="first:fx,second:fx")
    with pytest.raises(httpx.HTTPStatusError):
        translator._request_json("translate", {})
    assert calls == ["DeepL-Auth-Key first:fx"] * count


def test_deepl_rotation_does_not_consume_transient_retry_budget(translator_factory):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(456 if len(calls) < 3 else 200, json={"ok": True})

    translator = translator_factory(handle, provider="deepl", api_key="a:fx,b:fx,c:fx")
    assert translator._request_json("translate", {}, max_retries=1) == {"ok": True}
    assert len(calls) == 3


def test_deepl_keys_from_environment_and_empty_input(translator_factory, monkeypatch):
    monkeypatch.setenv("DEEPL_API_KEY", "  alpha:fx, beta:fx ,alpha:fx ")
    translator = translator_factory(Mock(), provider="deepl", api_key=None)
    assert translator._deepl_keys == ["alpha:fx", "beta:fx"]
    assert translator.api_key == "alpha:fx"
    empty = translator_factory(Mock(), provider="deepl", api_key=" , , ")
    with pytest.raises(ValueError, match="Missing API key"):
        empty._request_json("translate", {})


def test_deepl_cancel_between_keys_does_not_send_another_request(translator_factory):
    from concurrent.futures import CancelledError
    import threading
    cancelled = threading.Event()
    handle = Mock(return_value=httpx.Response(456))
    translator = translator_factory(handle, provider="deepl", api_key="a:fx,b:fx", cancel_event=cancelled,
                                    on_log=lambda *_: cancelled.set())
    with pytest.raises(CancelledError):
        translator._request_json("translate", {})
    assert handle.call_count == 1


@pytest.mark.parametrize("base_url, message", [
    ("https://api-free.deepl.com/v2", "500.000"),
    ("https://api.deepl.com/v2", "limite di spesa"),
])
def test_quota_error_is_actionable_and_preserves_cache_across_restart(
    translator_factory, base_url, message,
):
    calls = []
    retry = Mock()

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"translations": [{"text": "<text>Ciao</text>"}]})
        return httpx.Response(456)

    translator = translator_factory(handle, provider="deepl", base_url=base_url, on_retry_wait=retry)
    assert translator.translate_batch(["Hello"]) == ["Ciao"]
    with pytest.raises(DeepLQuotaExceededError) as caught:
        translator.translate_batch(["Goodbye"])
    assert message in str(caught.value)
    assert "test:fx" not in str(caught.value)
    assert caught.value.response.status_code == 456
    assert len(calls) == 2
    retry.assert_not_called()
    assert len(list(translator.cache_dir.glob("*_response.json"))) == 1

    unexpected_request = Mock(side_effect=AssertionError("Cached text must not call DeepL again"))
    restarted = translator_factory(unexpected_request, provider="deepl", base_url=base_url)
    assert restarted.translate_batch(["Hello"]) == ["Ciao"]
    unexpected_request.assert_not_called()


@pytest.mark.parametrize("header, expected", [
    ("120", 120.0),
    ("Tue, 14 Nov 2023 22:15:20 GMT", 120.0),
    ("invalid", 2.0), ("-5", 2.0), ("NaN", 2.0), ("inf", 2.0),
    ("Tue, 14 Nov 2023 22:00:00 GMT", 2.0),
])
def test_retry_after_seconds_dates_and_invalid_values(
    translator_factory, request_clock, monkeypatch, header, expected,
):
    monkeypatch.setattr("meowth.translator.time.time", lambda: 1700000000.0)
    calls = []

    def handle(request):
        calls.append(request_clock.now)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": header})
        return httpx.Response(200, json={"translations": [{"text": "<text>Ciao</text>"}]})

    translator = translator_factory(handle, provider="deepl")
    assert translator.translate_batch(["Hello"]) == ["Ciao"]
    assert calls[1] - calls[0] == expected


def test_deepl_recovers_after_multiple_limits_and_logs_without_secrets(
    translator_factory, request_clock, monkeypatch,
):
    monkeypatch.setattr("meowth.translator.random.uniform", lambda low, high: 0.5)
    calls, logs = [], []

    def handle(request):
        calls.append(request_clock.now)
        if len(calls) <= 4:
            return httpx.Response(429)
        return httpx.Response(200, json={"translations": [{"text": "<text>Ciao</text>"}]})

    translator = translator_factory(handle, provider="deepl", on_log=lambda *args: logs.append(args))
    assert translator.translate_batch(["Hello"]) == ["Ciao"]
    assert request_clock.waits == [2.5, 4.5, 8.5, 16.5]
    assert len(logs) == 4
    assert all(level == "warning" and "HTTP 429" in message for level, message in logs)
    assert "attempt 5/8" in logs[-1][1]
    assert "test:fx" not in str(logs)
    translator.translate_batch(["Hello"])
    assert len(calls) == 5  # Cache hit sends no further request or wait.


def test_deepl_concurrent_workers_share_pacing_and_cooldown(translator_factory, request_clock):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time

    start = threading.Barrier(4)
    calls = []
    active, peak = 0, 0

    def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(request_clock.now)
        time.sleep(0.01)  # Allow overlapping handlers if the request gate breaks.
        active -= 1
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "10"})
        return httpx.Response(200, json={"ok": True})

    translator = translator_factory(handle, provider="deepl")

    def send():
        start.wait(timeout=2)
        return translator._request_json("translate", {})

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(send) for _ in range(4)]
        assert all(future.result(timeout=3) == {"ok": True} for future in futures)
    assert peak == 1
    assert len(calls) == 5
    assert calls[1] - calls[0] == 10
    gaps = [after - before for before, after in zip(calls[1:], calls[2:])]
    assert all(gap >= 1 for gap in gaps)
    assert gaps[0] > gaps[-1]  # Successful calls gradually restore throughput.


def test_normal_deepl_calls_are_spaced(translator_factory, request_clock):
    calls = []

    def handle(request):
        calls.append(request_clock.now)
        return httpx.Response(200, json={})

    translator = translator_factory(handle, provider="deepl")
    for _ in range(3):
        translator._request_json("translate", {})
    assert calls == [1000, 1001, 1002]


def test_other_providers_keep_three_attempts(translator_factory, request_clock):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503)

    translator = translator_factory(handle, provider="deepseek")
    with pytest.raises(httpx.HTTPStatusError):
        translator._request_json("chat/completions", {})
    assert len(calls) == 3
    assert request_clock.waits == [2, 4]


def test_cancel_interrupts_retry_wait_and_engine_logs(tmp_path):
    from concurrent.futures import CancelledError, ThreadPoolExecutor
    import threading

    retry_logged = threading.Event()
    callbacks = Mock()
    callbacks.on_log.side_effect = lambda *args: retry_logged.set()
    engine = TranslationEngine(
        TranslationConfig(provider="deepl", api_key="test:fx", target_lang="it", work_dir=tmp_path),
        callbacks=callbacks,
    )
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    engine.translator._client.close()
    engine.translator._client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(engine.translator.translate_batch, ["Hello"])
            try:
                assert retry_logged.wait(timeout=2)
            finally:
                engine.cancel()
            with pytest.raises(CancelledError):
                future.result(timeout=1)
        assert len(calls) == 1
        assert "120.0s" in callbacks.on_log.call_args.args[1]
        callbacks.on_retry_wait.assert_called_once_with(120.0)
        assert not list(engine.translator.cache_dir.iterdir())
    finally:
        engine.close()


def test_cancel_interrupts_worker_waiting_for_request_gate(tmp_path):
    from concurrent.futures import CancelledError, ThreadPoolExecutor

    translator = Translator(provider="deepl", api_key="test:fx", cache_dir=tmp_path / "cache")
    translator._client.close()
    handler = Mock(return_value=httpx.Response(200, json={}))
    translator._client = httpx.Client(transport=httpx.MockTransport(handler))
    translator._request_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(translator._request_json, "translate", {})
            translator._cancel_event.set()
            with pytest.raises(CancelledError):
                future.result(timeout=1)
        handler.assert_not_called()
    finally:
        translator._request_lock.release()
        translator.close()


@pytest.mark.parametrize("source, translated", [
    ("Hi {C0}{C1}", "Ciao {C1}{C0}"),
    ("Hi {C0}", "Ciao"),
    ("Hi {P0}", "Ciao {P1}"),
    ("Hi {P0}", "Ciao {P0}{P0}"),
    ("Hi", "Ciao [rival]"),
    ("Hi", ""),
])
def test_invalid_translations_rejected(source, translated):
    with pytest.raises(TranslationValidationError):
        validate_translation(source, translated)


def test_name_order_can_change():
    validate_translation("{P0} follows {P1} {C0}", "{P1} è seguito da {P0} {C0}")


def test_llm_invalid_batch_retried_individually(translator_factory):
    responses = iter(["Ciao ||| Altro", "Ciao {C0}", "Altro {P0}"])
    translator = translator_factory(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": next(responses)}}]
    }))
    assert translator.translate_batch(["Hi {C0}", "Other {P0}"]) == ["Ciao {C0}", "Altro {P0}"]


def test_malformed_cache_is_repaired(translator_factory):
    translator = translator_factory(lambda request: httpx.Response(200, json={
        "translations": [{"text": "<text>Ciao {C0}</text>"}]
    }), provider="deepl")
    translator.translate_batch(["Hi {C0}"])
    path = next(translator.cache_dir.glob("*_response.json"))
    for corrupted in ['{"content":"Ciao"}', '{truncated']:
        path.write_text(corrupted, encoding="utf-8")
        assert translator.translate_batch(["Hi {C0}"]) == ["Ciao {C0}"]


def test_endpoint_is_part_of_cache_identity(translator_factory):
    def handle(request):
        return httpx.Response(200, json={"translations": [{"text": "<text>" + request.url.host + "</text>"}]})
    first = translator_factory(handle, provider="deepl", base_url="https://one.test/v2")
    second = translator_factory(handle, provider="deepl", base_url="https://two.test/v2")
    assert first.translate_batch(["Hello"]) != second.translate_batch(["Hello"])


def test_deepl_pro_and_explicit_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPL_API_KEY", "pro-key")
    for base, expected in [(None, "https://api.deepl.com/v2"), ("https://custom.test/v2/", "https://custom.test/v2")]:
        translator = Translator(provider="DEEPL", cache_dir=tmp_path, base_url=base)
        try:
            assert translator.api_key == "pro-key"
            assert translator.base_url == expected
        finally:
            translator.close()


def test_bundled_glossary_and_species_protection():
    glossary = Glossary(target_lang="it")
    assert glossary.lookup("thunderpunch") == "Tuonopugno"
    source = "PIKACHU, Mr. Mime, Farfetch’d, Nidoran♀ and Type: Null!"
    protected, names = glossary.protect_pokemon(source)
    assert len(names) == 5
    assert restore(protected, names) == source
    assert glossary.protect_pokemon("Pikachus")[0] == "Pikachus"
    assert glossary.get_context_terms("bite into this sandwich") == {}
    assert glossary.get_context_terms("Pikachus") == {}


def test_glossary_deepl_proper_nouns(translator_factory):
    def handle(request):
        body = json.loads(request.content)
        assert "<keep>Biancavilla</keep>" in body["text"][0]
        return httpx.Response(200, json={"translations": [{"text": body["text"][0]}]})
    translator = translator_factory(handle, provider="deepl")
    assert translator.translate_batch(["Pallet Town"], "  Pallet Town = Biancavilla") == ["Biancavilla"]


def test_italian_pipeline_preserves_hack_names_and_variables(tmp_path, translator_factory):
    def handle(request):
        batch = json.loads(request.content)["text"]
        assert all("EMBERCUB" not in t and "PIKACHU" not in t for t in batch)
        return httpx.Response(200, json={"translations": [
            {"text": t.replace("Hello", "Ciao").replace("Choose", "Scegli")} for t in batch
        ]})
    translator = translator_factory(handle, provider="deepl")
    config = TranslationConfig(target_lang="it", work_dir=tmp_path, max_workers=2)
    engine = TranslationEngine(config, translator=translator)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"entries": [
        {"original": "EMBERCUB", "category": "pokemon_names"},
        {"original": "PIKACHU", "category": "pokemon_names"},
        {"original": r"Hello [player]!\pChoose EMBERCUB!", "category": "scripts"},
    ]}), encoding="utf-8")
    output = engine.translate_texts(source, tmp_path / "translated.json")
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [entry["translated"] for entry in data["tables"][0]["entries"]] == ["EMBERCUB", "PIKACHU"]
    assert data["free_texts"][0]["translated"] == r"Ciao [player]!\pScegli EMBERCUB!"


def test_engine_rejects_invalid_injected_translator(tmp_path):
    translator = Mock()
    translator.translate_batch.return_value = ["Ciao"]
    callbacks = Mock()
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), callbacks, translator=translator)
    batch = [{"original": "Hello [player]"}]
    engine._translate_free_batch(batch)
    assert batch[0]["translated"] == "Hello [player]"
    assert callbacks.on_log.call_args.args[0] == "warning"


def test_engine_propagates_authentication_failure(tmp_path):
    translator = Mock()
    translator.translate_batch.side_effect = ValueError("Missing API key")
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=translator)
    with pytest.raises(ValueError, match="Missing API key"):
        engine._translate_free_batch([{"original": "Hello"}])


def test_controls_and_italian_wrapping():
    original = r"Hello\nworld\.\p[player]!\p"
    protected, codes = protect(original)
    assert restore(protected, codes) == r"Hello world\.\p[player]!\p"
    assert wrap_text(restore(protected, codes), target_lang="it") == r"Hello world\.\p[player]!\p"
    wrapped = wrap_text("Vai all'università perché è lì.", line_width=18, target_lang="it")
    assert "all'università" in wrapped and "perché" in wrapped
    assert "\\n " not in wrapped and " \\n" not in wrapped
    assert wrap_text(r"Ciao\CC010203mondo", target_lang="it") == r"Ciao\CC010203mondo"


def test_config_validation_and_defaults():
    config = TranslationConfig(target_lang="it", work_dir=None, output_dir=None)
    assert isinstance(config.work_dir, Path) and isinstance(config.output_dir, Path)
    with pytest.raises(ValueError, match="positive"):
        TranslationConfig(batch_size=0)


def test_cancel_prevents_build(tmp_path):
    from concurrent.futures import CancelledError
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=Mock())
    engine.cancel()
    with pytest.raises(CancelledError):
        engine.build_rom(tmp_path / "rom.gba", tmp_path / "texts.json", tmp_path / "out.gba")


def test_italian_rom_build_does_not_inject_chinese_manual_entries(tmp_path, monkeypatch):
    rom = tmp_path / "rom.gba"
    rom.write_bytes(b"\0" * 0xAC + b"BPRE")
    translations = tmp_path / "translations.json"
    translations.write_text(json.dumps({"entries": [
        {"original": "Hi", "translated": "Ciao", "category": "scripts"}
    ]}), encoding="utf-8")
    writer = Mock()
    writer.load_rom.return_value = bytearray(rom.read_bytes())
    writer.expand_rom.side_effect = lambda data: data
    writer.inject_texts.return_value = (bytearray(), {"in_place": 1, "relocated": 0, "skipped": 0})
    monkeypatch.setattr("meowth.core.engine.RomWriter", Mock(return_value=writer))
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=Mock())
    output = tmp_path / "new-directory/output.gba"
    engine.build_rom(rom, translations, output)
    entries = writer.inject_texts.call_args.args[1]
    assert len(entries) == 1 and entries[0]["translated"] == "Ciao"
    assert output.parent.exists()


def test_italian_unknown_trainer_classes_are_translated(tmp_path):
    translator = Mock()
    translator.translate_batch.return_value = ["Rivale"]
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=translator)
    table = {"category": "trainer_classes", "entries": [{"original": "RIVAL"}]}
    engine._translate_table(table)
    assert table["entries"][0]["translated"] == "Rivale"
    translator.translate_batch.assert_called_once()


def test_legacy_glossary_recovers_categories(tmp_path, monkeypatch):
    legacy = tmp_path / "glossary_en_it.json"
    legacy.write_text(json.dumps({"source_to_target": {"PIKACHU": "Pikachu"}}), encoding="utf-8")
    monkeypatch.setattr("meowth.glossary.get_resource_path", lambda name: legacy)
    glossary = Glossary(target_lang="it")
    assert glossary.protect_pokemon("PIKACHU")[0] == "{P0}"


def test_italian_encoding_preserves_accents_and_ellipsis():
    from meowth.charmap import Charmap
    charmap = Charmap(target_lang="it")
    assert charmap.can_encode("èéàìòù")[0]
    assert charmap.encode(r"\.") == bytes([0xB0, 0xFF])
    assert charmap.encode(wrap_text(r"\.")) == bytes([0xB0, 0xFF])


def test_unchanged_dialogue_is_not_cached(translator_factory):
    translator = translator_factory(lambda request: httpx.Response(200, json={
        "translations": [{"text": "<text>Hello friend</text>"}]
    }), provider="deepl")
    assert translator.translate_batch(["Hello friend"]) == ["Hello friend"]
    assert not list(translator.cache_dir.iterdir())


@pytest.mark.parametrize("response", [
    {"translations": []},
    {"translations": [{"text": "<text>broken"}]},
    {"translations": [{"text": "<text>Ciao</text>"}]},
])
def test_deepl_malformed_response_never_cached(translator_factory, response):
    translator = translator_factory(lambda request: httpx.Response(200, json=response), provider="deepl")
    with pytest.raises(TranslationValidationError):
        translator.translate_batch(["Hello {C0}"])
    assert not list(translator.cache_dir.iterdir())
