"""Container/codec checks for the Samsung RU7100 and FFmpeg remux/transcode fallbacks."""
import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .errors import PlaybackError

CONTAINERS = {
    "mov,mp4,m4a,3gp,3g2,mj2": "video/mp4",
    "matroska,webm": "video/x-matroska",
    "avi": "video/x-msvideo",
    "mpegts": "video/mp2t",
}
VIDEO_CODECS = {"h264", "hevc", "mpeg4", "mpeg2video", "vp8", "vp9", "mjpeg", "vc1", "wmv3"}
AUDIO_CODECS = {"aac", "ac3", "eac3", "mp3", "mp2", "vorbis", "opus", "wmav2", "pcm_s16le", "pcm_s24le"}
MP4_VIDEO = {"h264", "hevc", "mpeg4"}
MP4_AUDIO = {"aac", "ac3", "eac3", "mp3"}
MAX_WIDTH, MAX_HEIGHT = 3840, 2160


@dataclass
class Plan:
    action: str
    container: str
    mime: str
    reasons: list = field(default_factory=list)
    convert_video: bool = False
    convert_audio: bool = False
    subtitles: list = field(default_factory=list)


def tool(settings, name):
    configured = settings.get(name) or name
    found = shutil.which(configured)
    if found:
        return found
    for candidate in (config.LOCAL / "ffmpeg" / "bin" / f"{name}.exe", config.LOCAL / "ffmpeg" / "bin" / name):
        if candidate.is_file():
            return str(candidate)
    return None


async def probe(settings, path):
    ffprobe = tool(settings, "ffprobe")
    if ffprobe is None:
        return None
    process = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise PlaybackError(
            f"The downloaded file could not be read as video ({stderr.decode(errors='replace').strip()[:200]}).",
            "bad_media",
        )
    return json.loads(stdout or b"{}")


async def probe_header(settings, path, limit=16 * 1024 * 1024):
    """Probe a prefix copy of a growing file. None when the start of the file is not playable yet."""
    ffprobe = tool(settings, "ffprobe")
    if ffprobe is None:
        return None
    sample = path.with_name(path.name + ".headerprobe")
    try:
        with path.open("rb") as src, sample.open("wb") as dst:
            copied = 0
            while copied < limit:
                block = src.read(min(1024 * 1024, limit - copied))
                if not block:
                    break
                dst.write(block)
                copied += len(block)
        if copied < 1024 * 1024:
            return None
        process = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json",
            "-probesize", str(copied), "-analyzeduration", "2000000", str(sample),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        try:
            data = json.loads(stdout or b"{}")
        except json.JSONDecodeError:
            return None
        streams = data.get("streams") or []
        if not any(stream.get("codec_type") == "video" for stream in streams):
            return None
        return data
    finally:
        sample.unlink(missing_ok=True)


TAIL_INDEX_MAX = 48 * 1024 * 1024


def _atom(data, pos):
    """Return (size, header_len, type) for the atom at pos, or None when the header is incomplete."""
    if pos + 8 > len(data):
        return None
    size = int.from_bytes(data[pos:pos + 4], "big")
    kind = bytes(data[pos + 4:pos + 8])
    header_len = 8
    if size == 1:
        if pos + 16 > len(data):
            return None
        size = int.from_bytes(data[pos + 8:pos + 16], "big")
        header_len = 16
    if size != 0 and size < header_len:
        return None
    return size, header_len, kind


def mp4_tail_index(header, file_size):
    """Return (mdat_start, mdat_end) when the MP4 index sits after the media and is small enough to fetch first.

    None for a fast-start file, a non-MP4 prefix, or an index that cannot be located from this prefix.
    """
    if len(header) < 12 or header[4:8] != b"ftyp" or not file_size:
        return None
    pos = 0
    saw_moov = False
    while pos + 8 <= len(header):
        atom = _atom(header, pos)
        if atom is None:
            return None
        size, _header_len, kind = atom
        if kind == b"moov":
            saw_moov = True
        if kind == b"mdat":
            if size == 0 or saw_moov:
                return None
            end = pos + size
            tail = file_size - end
            if tail <= 8 or tail > TAIL_INDEX_MAX:
                return None
            return pos, end
        if size == 0 or pos + size > len(header):
            return None
        pos += size
    return None


_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


def _patch_offsets(buf, start, end, shift):
    """Add shift to chunk offsets inside this atom span. None on a layout we should not rewrite."""
    pos = start
    found = 0
    while pos + 8 <= end:
        atom = _atom(buf, pos)
        if atom is None:
            return None
        size, header_len, kind = atom
        if size == 0 or pos + size > end:
            return None
        if kind == b"cmov":
            return None
        if kind in _CONTAINERS:
            inner = _patch_offsets(buf, pos + header_len, pos + size, shift)
            if inner is None:
                return None
            found += inner
        elif kind in (b"stco", b"co64"):
            width = 8 if kind == b"co64" else 4
            base = pos + header_len
            if buf[base] != 0 or base + 8 > pos + size:
                return None
            count = int.from_bytes(buf[base + 4:base + 8], "big")
            entries = base + 8
            if entries + count * width > pos + size:
                return None
            for index in range(count):
                at = entries + index * width
                moved = int.from_bytes(buf[at:at + width], "big") + shift
                if width == 4 and moved > 0xFFFFFFFF:
                    return None
                buf[at:at + width] = moved.to_bytes(width, "big")
            found += 1
        pos += size
    return found


def place_index_first(prefix, tail):
    """Return prefix plus tail with chunk offsets moved so the media can follow this header.

    None when the index cannot be rewritten. The TV reads a file from the front, so an index
    that only exists at the end is not enough for it to start.
    """
    if not prefix or not tail or len(tail) < 8:
        return None
    buf = bytearray(tail)
    pos = 0
    found = 0
    while pos + 8 <= len(buf):
        atom = _atom(buf, pos)
        if atom is None:
            return None
        size, header_len, kind = atom
        if size == 0 or pos + size > len(buf):
            return None
        if kind == b"moov":
            patched = _patch_offsets(buf, pos + header_len, pos + size, len(tail))
            if not patched:
                return None
            found += patched
        pos += size
    if not found:
        return None
    return prefix + bytes(buf)


def _before_mdat(header):
    pos = 0
    while pos + 8 <= len(header):
        atom = _atom(header, pos)
        if atom is None:
            return None
        size, _header_len, kind = atom
        if kind == b"mdat":
            return header[:pos]
        if size == 0 or pos + size > len(header):
            return None
        pos += size
    return None


async def probe_indexed(settings, path, index):
    """Probe an MP4 whose index was fetched from the end and is not in the growing file yet."""
    ffprobe = tool(settings, "ffprobe")
    if ffprobe is None or not index:
        return None
    sample = path.with_name(path.name + ".headerprobe")
    try:
        with path.open("rb") as src:
            prefix = _before_mdat(src.read(1024 * 1024))
        if not prefix:
            return None
        with sample.open("wb") as dst:
            dst.write(prefix)
            dst.write(index)
        process = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json",
            "-probesize", str(len(prefix) + len(index)), "-analyzeduration", "2000000", str(sample),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        try:
            data = json.loads(stdout or b"{}")
        except json.JSONDecodeError:
            return None
        streams = data.get("streams") or []
        if not any(stream.get("codec_type") == "video" for stream in streams):
            return None
        return data
    finally:
        sample.unlink(missing_ok=True)


def duration_of(data):
    try:
        return int(float(data["format"]["duration"]))
    except (KeyError, TypeError, ValueError):
        return None


def video_ok(stream, reasons):
    codec = stream.get("codec_name")
    if codec not in VIDEO_CODECS:
        reasons.append(f"video codec {codec} is not supported")
        return False
    if (stream.get("width") or 0) > MAX_WIDTH or (stream.get("height") or 0) > MAX_HEIGHT:
        reasons.append("video is larger than 4K")
        return False
    pix_fmt = stream.get("pix_fmt") or ""
    level = stream.get("level") or 0
    if codec == "h264":
        if "10" in pix_fmt or "422" in pix_fmt or "444" in pix_fmt:
            reasons.append(f"H.264 {pix_fmt} is not supported")
            return False
        if level > 51:
            reasons.append(f"H.264 level {level / 10:.1f} is above 5.1")
            return False
    if codec == "hevc":
        if "422" in pix_fmt or "444" in pix_fmt or "12" in pix_fmt:
            reasons.append(f"HEVC {pix_fmt} is not supported")
            return False
        if level > 153:
            reasons.append("HEVC level is above 5.1")
            return False
    return True


def plan_for(data, source_mime):
    """Decide how to make data playable on the TV. data is ffprobe JSON; None means unknown."""
    if data is None:
        return Plan("direct", "", source_mime or "video/mp4", ["format not checked (ffprobe not installed)"])
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if video is None:
        raise PlaybackError("The downloaded file has no video track.", "bad_media")
    reasons = []
    convert_video = not video_ok(video, reasons)
    convert_audio = False
    for stream in audio:
        if stream.get("codec_name") not in AUDIO_CODECS:
            reasons.append(f"audio codec {stream.get('codec_name')} is not supported")
            convert_audio = True
    format_name = data.get("format", {}).get("format_name", "")
    container_mime = CONTAINERS.get(format_name)
    if container_mime is None:
        reasons.append(f"container {format_name} is not supported")
    if not convert_video and not convert_audio and container_mime:
        return Plan("direct", format_name, container_mime, reasons)
    video_codec = "h264" if convert_video else video.get("codec_name")
    audio_codecs = {"ac3" if convert_audio else s.get("codec_name") for s in audio}
    mp4 = video_codec in MP4_VIDEO and audio_codecs <= MP4_AUDIO
    # Matroska preserves subtitle tracks without burning them into the picture.
    # mov_text cannot be copied to Matroska, so convert that text track to SRT.
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle" and
                 s.get("codec_name") in {"subrip", "ass", "ssa", "webvtt", "mov_text", "text",
                                        "hdmv_pgs_subtitle", "dvd_subtitle"}]
    container, mime = ("mp4", "video/mp4") if mp4 and not subtitles else ("mkv", "video/x-matroska")
    action = "transcode" if convert_video else "audio" if convert_audio else "remux"
    return Plan(action, container, mime, reasons, convert_video, convert_audio, subtitles)


async def convert(settings, source, destination, plan, duration, progress):
    """Run FFmpeg. progress(fraction or None) is called as output advances."""
    ffmpeg = tool(settings, "ffmpeg")
    if ffmpeg is None:
        raise PlaybackError(
            "This video needs converting for the TV, but FFmpeg is not installed on the PC.", "unsupported"
        )
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?"]
    for index, stream in enumerate(plan.subtitles):
        args += ["-map", f"0:{stream['index']}", f"-c:s:{index}",
                 "srt" if stream.get("codec_name") in {"mov_text", "text"} else "copy"]
    if not plan.subtitles:
        args += ["-sn"]
    if plan.convert_video:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-profile:v", "high", "-level", "4.1"]
    else:
        args += ["-c:v", "copy"]
    if plan.convert_audio:
        args += ["-c:a", "ac3", "-b:a", "640k"]
    else:
        args += ["-c:a", "copy"]
    if plan.container == "mp4":
        args += ["-movflags", "+faststart", "-f", "mp4"]
    else:
        args += ["-f", "matroska"]
    args += ["-progress", "pipe:1", "-nostats", str(destination)]
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stderr_tail = []

    async def read_stderr():
        async for line in process.stderr:
            stderr_tail.append(line.decode(errors="replace").rstrip())
            del stderr_tail[:-20]

    stderr_task = asyncio.create_task(read_stderr())
    try:
        async for raw in process.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                value = line.split("=", 1)[1]
                if value.isdigit() and duration:
                    progress(min(1.0, int(value) / 1_000_000 / duration))
        await process.wait()
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    finally:
        await stderr_task
    if process.returncode != 0:
        detail = next((line for line in reversed(stderr_tail) if line), "unknown error")
        raise PlaybackError(f"Converting the video failed: {detail[:200]}", "convert_failed", retryable=True)


def output_name(source: Path, plan):
    return f"{source.stem}.tv.{plan.container}"
