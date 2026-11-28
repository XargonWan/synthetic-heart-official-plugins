"""The Vosk hearing extension: model choice, download and unpacking, audio conversion,
transcription (against a fake Kaldi recognizer) and the extension's operations."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import shutil
import sys
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.extensions.manifest import load_manifest

SOURCE = Path(__file__).resolve().parent.parent


def _load() -> tuple[Any, Any]:
    sys.path.insert(0, str(SOURCE))
    try:
        return importlib.import_module("vosk_stt"), importlib.import_module("ext_main")
    finally:
        sys.path.remove(str(SOURCE))
        for name in ("vosk_stt", "ext_main"):
            sys.modules.pop(name, None)


vosk_stt, ext_main = _load()


def wav_bytes(rate: int = 16000, channels: int = 1, seconds: float = 0.3) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x01\x00" * int(rate * seconds) * channels)
    return buffer.getvalue()


def model_zip(top: str = "vosk-model-small-en-us-0.15", *, evil: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{top}/am/final.mdl", "model")
        zf.writestr(f"{top}/conf/mfcc.conf", "conf")
        if evil:
            zf.writestr(f"{top}/../../escaped.txt", "no")
    return buffer.getvalue()


class TestManifest:
    def test_it_declares_the_engine_and_the_protocol(self) -> None:
        manifest = load_manifest(SOURCE / "synth-extension.toml")
        assert manifest.id == "official/auris_vosk"
        assert manifest.provides.endpoints == ("vosk",)
        (protocol,) = manifest.provides.protocols
        assert (protocol.name, protocol.base) == ("vosk", "")
        assert "stt" in protocol.ops and "tts" not in protocol.ops
        assert (SOURCE / "guide.md").is_file() and (SOURCE / "icon.svg").is_file()
        assert "vosk>=0.3.45" in manifest.python_requirements


class TestModelChoice:
    @pytest.mark.parametrize(
        "language, expected",
        [
            ("en", "vosk-en-us"),
            ("it", "vosk-it-it"),
            ("it-IT", "vosk-it-it"),
            ("IT_it", "vosk-it-it"),
            ("zh", "vosk-zh-cn"),
            ("auto", "vosk-en-us"),
            ("", "vosk-en-us"),
            (None, "vosk-en-us"),
            ("xx", "vosk-en-us"),
        ],
    )
    def test_the_language_picks_its_small_model(
        self, language: Any, expected: str
    ) -> None:
        assert vosk_stt.model_for(language) == expected

    def test_an_explicit_known_model_wins_and_an_unknown_one_is_ignored(self) -> None:
        assert vosk_stt.model_for("it", "vosk-en-us-large") == "vosk-en-us-large"
        assert vosk_stt.model_for("it", "no-such-model") == "vosk-it-it"

    def test_the_catalogue_is_described_for_the_picker(self) -> None:
        infos = vosk_stt.model_infos()
        assert {i["id"] for i in infos} == set(vosk_stt.MODELS)
        assert all(i["capabilities"] == {"auris": True} for i in infos)
        assert all(
            vosk_stt.LANGUAGE_MODEL[tag] in vosk_stt.MODELS
            for tag in vosk_stt.LANGUAGE_MODEL
        )


class TestDownload:
    def _serve(self, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
        import httpx

        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            return httpx.Response(200, content=payload)

        @contextlib.contextmanager
        def stream(method: str, url: str, **kw: Any) -> Any:
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with client.stream(method, url) as response:
                    yield response

        monkeypatch.setattr(httpx, "stream", stream)
        return asked

    def test_a_model_is_downloaded_unpacked_without_its_top_folder_and_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = self._serve(monkeypatch, model_zip())
        path = vosk_stt.ensure_model("vosk-en-us", tmp_path)
        assert path == tmp_path / "vosk-en-us"
        assert (path / "am" / "final.mdl").read_text() == "model"
        assert asked == [
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        ]
        vosk_stt.ensure_model("vosk-en-us", tmp_path)
        assert len(asked) == 1  # not fetched again
        assert not list(tmp_path.glob("*.part")) and not list(
            tmp_path.glob("*.staging")
        )

    def test_an_archive_that_climbs_out_of_its_folder_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._serve(monkeypatch, model_zip(evil=True))
        with pytest.raises(ValueError, match="unsafe path"):
            vosk_stt.ensure_model("vosk-en-us", tmp_path)
        assert not (tmp_path.parent / "escaped.txt").exists()
        assert not (tmp_path / "vosk-en-us").exists()

    def test_something_that_is_not_a_model_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("top/readme.txt", "hello")
        self._serve(monkeypatch, buffer.getvalue())
        with pytest.raises(ValueError, match="not a Vosk model"):
            vosk_stt.ensure_model("vosk-en-us", tmp_path)
        assert not (tmp_path / "vosk-en-us").exists()


class TestAudio:
    def test_a_16k_mono_wav_is_read_as_it_is(self, tmp_path: Path) -> None:
        source = tmp_path / "in.wav"
        source.write_bytes(wav_bytes())
        path, converted = vosk_stt.to_wav_16k(source)
        assert path == source and converted is False

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
    )
    def test_another_rate_goes_through_ffmpeg(self, tmp_path: Path) -> None:
        source = tmp_path / "in.wav"
        source.write_bytes(wav_bytes(rate=24000, channels=2))
        path, converted = vosk_stt.to_wav_16k(source)
        assert converted is True and path != source
        with wave.open(str(path), "rb") as wf:
            assert (wf.getframerate(), wf.getnchannels()) == (16000, 1)

    @pytest.mark.skipif(
        shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
    )
    def test_a_file_ffmpeg_cannot_read_is_an_error_and_leaves_nothing(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "in.bin"
        source.write_bytes(b"not audio at all")
        with pytest.raises(RuntimeError, match="ffmpeg could not read"):
            vosk_stt.to_wav_16k(source)
        assert [p.name for p in tmp_path.iterdir()] == ["in.bin"]

    def test_a_missing_ffmpeg_is_said_plainly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "in.bin"
        source.write_bytes(b"x")

        def boom(*a: Any, **k: Any) -> Any:
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(vosk_stt.subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
            vosk_stt.to_wav_16k(source)
        assert [p.name for p in tmp_path.iterdir()] == ["in.bin"]


class FakeRecognizer:
    """Says "hello" at the first full chunk and "world" at the end."""

    def __init__(self, model: Any, rate: int) -> None:
        self.model, self.rate, self.chunks = model, rate, 0

    def SetWords(self, flag: bool) -> None:  # noqa: N802 - the Vosk API's name
        pass

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802
        self.chunks += 1
        return self.chunks == 1

    def Result(self) -> str:  # noqa: N802
        return '{"text": "hello"}'

    def FinalResult(self) -> str:  # noqa: N802
        return '{"text": "world"}'


class TestTranscribe:
    def _audio(self, tmp_path: Path) -> Path:
        source = tmp_path / "speech.wav"
        source.write_bytes(wav_bytes(seconds=1.0))
        return source

    def test_the_parts_are_joined(self, tmp_path: Path) -> None:
        models = tmp_path / "models"
        (models / "vosk-it-it" / "am").mkdir(parents=True)
        seen: list[str] = []
        vosk_stt._loaded.clear()
        text = vosk_stt.transcribe(
            self._audio(tmp_path),
            language="it",
            directory=models,
            factory=lambda path: seen.append(path) or SimpleNamespace(path=path),
            recognizer=FakeRecognizer,
        )
        assert text == "hello world"
        assert seen == [str(models / "vosk-it-it")]

    def test_a_model_is_loaded_once(self, tmp_path: Path) -> None:
        models = tmp_path / "models"
        (models / "vosk-en-us" / "am").mkdir(parents=True)
        loads: list[str] = []
        vosk_stt._loaded.clear()
        for _ in range(2):
            vosk_stt.transcribe(
                self._audio(tmp_path),
                language="en",
                directory=models,
                factory=lambda path: loads.append(path) or object(),
                recognizer=FakeRecognizer,
            )
        assert len(loads) == 1

    def test_silence_is_none(self, tmp_path: Path) -> None:
        class Silent(FakeRecognizer):
            def Result(self) -> str:  # noqa: N802
                return '{"text": ""}'

            def FinalResult(self) -> str:  # noqa: N802
                return '{"text": ""}'

        models = tmp_path / "models"
        (models / "vosk-en-us" / "am").mkdir(parents=True)
        vosk_stt._loaded.clear()
        assert (
            vosk_stt.transcribe(
                self._audio(tmp_path),
                language="en",
                directory=models,
                factory=lambda path: object(),
                recognizer=Silent,
            )
            is None
        )

    def test_a_model_that_cannot_be_had_is_none_not_an_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vosk_stt._loaded.clear()
        monkeypatch.setattr(
            vosk_stt,
            "ensure_model",
            lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
        )
        assert vosk_stt.transcribe(self._audio(tmp_path), language="en") is None


class FakeCtx:
    def __init__(self, tmp: Path, config: dict[str, Any] | None = None) -> None:
        self.data_dir = tmp
        self.values = config or {}
        self.stopping = asyncio.Event()
        self.handler: Any = None
        self.ensured: list[tuple[Any, ...]] = []
        ctx = self

        class Protocols:
            def on_request(self, handler: Any) -> None:
                ctx.handler = handler

        class Endpoints:
            async def ensure(
                self, name: str, base_url: str, **kwargs: Any
            ) -> dict[str, Any]:
                ctx.ensured.append((name, base_url, kwargs))
                return {"created": True}

        class Config:
            async def all(self) -> dict[str, Any]:
                return dict(ctx.values)

        self.protocols, self.endpoints, self.config = Protocols(), Endpoints(), Config()
        self.log = SimpleNamespace(info=lambda m: None, error=lambda m: None)


@pytest.mark.asyncio
async def test_the_engine_endpoint_is_created_local_with_the_hearing_subsystem(
    tmp_path: Path,
) -> None:
    ctx = FakeCtx(tmp_path)
    ctx.stopping.set()
    await ext_main.main(ctx)
    ((name, base_url, kwargs),) = ctx.ensured
    assert (name, base_url) == ("vosk", "")
    assert kwargs["protocol"] == "vosk" and kwargs["subsystem_map"] == {"auris": True}


@pytest.mark.asyncio
async def test_operations_and_the_settings_reach_the_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = FakeCtx(tmp_path, {"VOSK_LANGUAGE": "fr", "VOSK_MODEL": ""})
    ctx.stopping.set()
    await ext_main.main(ctx)
    ask = ctx.handler
    assert {m["id"] for m in await ask("vosk", "models", {}, "", {})} == set(
        vosk_stt.MODELS
    )
    assert (await ask("vosk", "capabilities", {}, "", {}))["auris"] is True
    assert await ask("vosk", "health", {}, "", {}) is True
    assert (await ask("vosk", "ping", {}, "", {}))["ok"] is False
    with pytest.raises(ValueError, match="not provided"):
        await ask("vosk", "tts", {}, "", {})

    calls: list[tuple[Path, dict[str, Any]]] = []

    def fake(audio: Path, **kw: Any) -> str:
        calls.append((audio, kw))
        return "bonjour"

    monkeypatch.setattr(vosk_stt, "transcribe", fake)
    got = await ask("vosk", "stt", {}, "", {"path": "/data/a.wav", "kwargs": {}})
    assert got == {"text": "bonjour"}
    assert calls[0][0] == Path("/data/a.wav")
    assert calls[0][1]["language"] == "fr"
    # a language or model the call names wins over the setting
    await ask(
        "vosk",
        "stt",
        {},
        "",
        {
            "path": "/data/b.wav",
            "kwargs": {"language": "de", "model": "vosk-en-us-large"},
        },
    )
    assert calls[1][1] == {"language": "de", "explicit_model": "vosk-en-us-large"}


@pytest.mark.asyncio
async def test_nothing_recognised_is_an_empty_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = FakeCtx(tmp_path)
    ctx.stopping.set()
    await ext_main.main(ctx)
    monkeypatch.setattr(vosk_stt, "transcribe", lambda *a, **k: None)
    assert await ctx.handler(
        "vosk", "stt", {}, "", {"path": "/x.wav", "kwargs": {}}
    ) == {"text": ""}
