from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import struct
import tempfile
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import discord
import imageio_ffmpeg
import pyttsx3
from discord.ext import voice_recv
from faster_whisper import WhisperModel

log = logging.getLogger("acegpt.voice")

SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2
FRAME_MS = 20
RMS_THRESHOLD = max(50, int(os.getenv("VOICE_RMS_THRESHOLD", "350")))
SILENCE_FRAMES = max(10, int(float(os.getenv("VOICE_SILENCE_SECONDS", "0.8")) * 1000 / FRAME_MS))
MAX_FRAMES = max(50, int(float(os.getenv("VOICE_MAX_UTTERANCE_SECONDS", "12")) * 1000 / FRAME_MS))
MIN_SPEECH_FRAMES = max(5, int(0.3 * 1000 / FRAME_MS))
WHISPER_MODEL_NAME = os.getenv("VOICE_WHISPER_MODEL", "base.en").strip()
VOICE_LANGUAGE = os.getenv("VOICE_LANGUAGE", "en").strip() or None

_model: WhisperModel | None = None
_model_lock = threading.Lock()
_tts_lock = threading.Lock()
VOICE_TTS_ENABLED = os.getenv("VOICE_TTS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
VOICE_TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "").strip()
VOICE_TTS_RATE = max(80, min(300, int(os.getenv("VOICE_TTS_RATE", "180"))))
VOICE_TTS_VOLUME = max(0.0, min(1.0, float(os.getenv("VOICE_TTS_VOLUME", "1.0"))))


def _synthesize_speech(text: str) -> str:
    """Create a temporary WAV using the host's offline speech engine."""
    handle = tempfile.NamedTemporaryFile(prefix=".acegpt-tts-", suffix=".wav", dir=".", delete=False)
    path = os.path.abspath(handle.name)
    handle.close()
    relative_path = os.path.relpath(path, os.getcwd())

    try:
        with _tts_lock:
            engine = pyttsx3.init()
            if VOICE_TTS_VOICE:
                engine.setProperty("voice", VOICE_TTS_VOICE)
            engine.setProperty("rate", VOICE_TTS_RATE)
            engine.setProperty("volume", VOICE_TTS_VOLUME)
            engine.save_to_file(text, relative_path)
            engine.runAndWait()
            engine.stop()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError("The local speech engine did not create audio.")
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


async def _play_speech(voice_client: discord.VoiceClient, text: str) -> None:
    path = await asyncio.to_thread(_synthesize_speech, text)
    try:
        source = discord.FFmpegOpusAudio(path, executable=imageio_ffmpeg.get_ffmpeg_exe())
        loop = asyncio.get_running_loop()
        finished: asyncio.Future[None] = loop.create_future()

        def complete(error: Exception | None) -> None:
            if finished.done():
                return
            if error:
                finished.set_exception(error)
            else:
                finished.set_result(None)

        def after(error: Exception | None) -> None:
            loop.call_soon_threadsafe(complete, error)

        voice_client.play(source, after=after)
        await finished
    finally:
        try:
            os.remove(path)
        except OSError:
            log.warning("Could not remove temporary speech audio file")


def _get_whisper_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            log.info("Loading local speech recognition model '%s'", WHISPER_MODEL_NAME)
            _model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
            log.info("Local speech recognition model is ready")
        return _model


def _transcribe_pcm(pcm: bytes) -> str:
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    audio.seek(0)
    segments, _info = _get_whisper_model().transcribe(
        audio,
        language=VOICE_LANGUAGE,
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=False,
        initial_prompt="AceGPT, a voice assistant. People may start questions by saying Ace or AceGPT.",
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def _extract_question(transcript: str) -> str | None:
    words = re.findall(r"[a-z0-9]+", transcript.lower())
    configured = re.findall(r"[a-z0-9]+", os.getenv("VOICE_WAKE_WORD", "acegpt").lower())
    if not configured:
        configured = ["acegpt"]
    variants = [configured]
    if configured == ["acegpt"]:
        variants.extend((["ace", "gpt"], ["ace"], ["a", "gpt"], ["hey", "ace"], ["hey", "ace", "gpt"], ["hey", "acegpt"]))
    else:
        variants.append(["hey", *configured])

    for phrase in sorted(variants, key=len, reverse=True):
        if words[:len(phrase)] == phrase:
            question = " ".join(words[len(phrase):]).strip()
            return question or "Please say hello and ask what you need."
    return None


@dataclass
class _SpeakerBuffer:
    preroll: deque[bytes] = field(default_factory=lambda: deque(maxlen=6))
    frames: list[bytes] = field(default_factory=list)
    speech_frames: int = 0
    silence_frames: int = 0

    @property
    def active(self) -> bool:
        return bool(self.frames)

    def reset(self) -> None:
        self.frames.clear()
        self.speech_frames = 0
        self.silence_frames = 0
        self.preroll.clear()


class QuestionSink(voice_recv.AudioSink):
    """Split received PCM into short utterances; retain audio only in memory."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_utterance: Callable[[discord.abc.User, bytes], None]):
        super().__init__()
        self.loop = loop
        self.on_utterance = on_utterance
        self.states: dict[int, _SpeakerBuffer] = {}
        self.lock = threading.Lock()

    def wants_opus(self) -> bool:
        return False

    @staticmethod
    def _rms(pcm: bytes) -> float:
        sample_count = len(pcm) // SAMPLE_WIDTH
        if not sample_count:
            return 0.0
        samples = struct.unpack(f"<{sample_count}h", pcm[:sample_count * SAMPLE_WIDTH])
        return math.sqrt(sum(sample * sample for sample in samples) / sample_count)

    def write(self, user: discord.abc.User | None, data: voice_recv.VoiceData) -> None:
        if user is None or user.bot or not data.pcm:
            return
        pcm = data.pcm
        active_voice = self._rms(pcm) >= RMS_THRESHOLD
        completed: bytes | None = None

        with self.lock:
            speaker = self.states.setdefault(user.id, _SpeakerBuffer())
            if not speaker.active:
                speaker.preroll.append(pcm)
                if active_voice:
                    speaker.frames = list(speaker.preroll)
                    speaker.speech_frames = 1
                    speaker.silence_frames = 0
            else:
                speaker.frames.append(pcm)
                if active_voice:
                    speaker.speech_frames += 1
                    speaker.silence_frames = 0
                else:
                    speaker.silence_frames += 1
                if speaker.silence_frames >= SILENCE_FRAMES or len(speaker.frames) >= MAX_FRAMES:
                    if speaker.speech_frames >= MIN_SPEECH_FRAMES:
                        completed = b"".join(speaker.frames)
                    speaker.reset()

        if completed:
            try:
                self.loop.call_soon_threadsafe(self.on_utterance, user, completed)
            except RuntimeError:
                log.debug("Discarding voice audio after the event loop closed")

    def cleanup(self) -> None:
        with self.lock:
            self.states.clear()


@dataclass
class VoiceSession:
    guild_id: int
    voice_channel: discord.VoiceChannel | discord.StageChannel
    text_channel: discord.abc.Messageable
    voice_client: voice_recv.VoiceRecvClient
    sink: QuestionSink
    queue: asyncio.Queue[tuple[discord.abc.User, bytes]]
    worker: asyncio.Task[None]


AnswerCallback = Callable[[int, str, str, str | None], Awaitable[str]]


class VoiceAssistant:
    def __init__(self, answer_question: AnswerCallback):
        self.answer_question = answer_question
        self.sessions: dict[int, VoiceSession] = {}

    async def join(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        text_channel: discord.abc.Messageable,
    ) -> None:
        existing = self.sessions.get(guild.id)
        if existing:
            if existing.voice_channel.id != voice_channel.id:
                await existing.voice_client.move_to(voice_channel)
                existing.voice_channel = voice_channel
            existing.text_channel = text_channel
            return

        current_client = guild.voice_client
        if current_client:
            await current_client.disconnect(force=True)

        voice_client = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[discord.abc.User, bytes]] = asyncio.Queue(maxsize=12)
        sink = QuestionSink(loop, lambda user, audio: self._queue_audio(guild.id, user, audio))
        worker = asyncio.create_task(self._answer_loop(guild.id, voice_channel.id, queue), name=f"acegpt-voice-{guild.id}")
        session = VoiceSession(guild.id, voice_channel, text_channel, voice_client, sink, queue, worker)
        self.sessions[guild.id] = session
        try:
            voice_client.listen(sink)
        except Exception:
            self.sessions.pop(guild.id, None)
            worker.cancel()
            await voice_client.disconnect(force=True)
            raise
        log.info("Joined voice channel %s in guild %s", voice_channel.id, guild.id)

    def _queue_audio(self, guild_id: int, user: discord.abc.User, audio: bytes) -> None:
        session = self.sessions.get(guild_id)
        if session is None:
            return
        if session.queue.full():
            log.warning("Dropping a voice utterance because the processing queue is full in guild %s", guild_id)
            return
        session.queue.put_nowait((user, audio))

    async def _answer_loop(self, guild_id: int, voice_channel_id: int, queue: asyncio.Queue[tuple[discord.abc.User, bytes]]) -> None:
        while True:
            user, audio = await queue.get()
            try:
                transcript = await asyncio.to_thread(_transcribe_pcm, audio)
                question = _extract_question(transcript)
                if question is None:
                    continue
                log.info("Voice wake phrase detected in guild %s", guild_id)
                answer = await self.answer_question(voice_channel_id, user.display_name, question, None)
                session = self.sessions.get(guild_id)
                if session is None:
                    continue
                if VOICE_TTS_ENABLED:
                    try:
                        await _play_speech(session.voice_client, answer)
                        log.info("Spoke voice answer in guild %s", guild_id)
                    except Exception:
                        log.exception("Could not speak voice answer in guild %s", guild_id)
                        await session.text_channel.send(f"🔈 I couldn't speak that answer, so here's the text:\n{answer[:1900]}")
                else:
                    for start in range(0, len(answer), 1900):
                        await session.text_channel.send(f"🎙️ **{discord.utils.escape_markdown(user.display_name)}:** {answer[start:start + 1900]}")
                    log.info("Posted voice answer in text channel for guild %s", guild_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Voice question processing failed in guild %s", guild_id)
            finally:
                queue.task_done()

    async def leave(self, guild_id: int) -> bool:
        session = self.sessions.pop(guild_id, None)
        if session is None:
            return False
        session.voice_client.stop_listening()
        session.worker.cancel()
        try:
            await session.worker
        except asyncio.CancelledError:
            pass
        await session.voice_client.disconnect(force=True)
        log.info("Left voice channel in guild %s", guild_id)
        return True

    async def handle_disconnect(self, guild_id: int) -> None:
        session = self.sessions.pop(guild_id, None)
        if session:
            session.worker.cancel()
            try:
                await session.worker
            except asyncio.CancelledError:
                pass
            log.info("Voice session ended after Discord disconnected AceGPT in guild %s", guild_id)

