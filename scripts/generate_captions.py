"""CPU-limited local Whisper worker. Run separately so shutdown can stop inference."""
import argparse
import os
from pathlib import Path


def timestamp(seconds):
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=max(1, args.threads),
                         download_root=str(Path(__file__).resolve().parents[1] / ".local" / "whisper-models"))
    segments, _ = model.transcribe(args.source, vad_filter=True, task="translate")
    cues = []
    for segment in segments:
        text = segment.text.strip().replace("-->", "→")
        if text and segment.end > segment.start:
            cues.append(f"{len(cues)+1}\n{timestamp(segment.start)} --> {timestamp(segment.end)}\n{text}\n")
    if not cues:
        raise ValueError("No speech was detected")
    content = "\n".join(cues)
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Captions exceed 2 MB")
    temporary = args.destination.with_suffix(".srt.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, args.destination)


if __name__ == "__main__":
    main()
