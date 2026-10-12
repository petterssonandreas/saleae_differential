from dataclasses import asdict
from pathlib import Path
import csv
import json
import shutil
import tempfile
from .binary import read_analog
from .models import CaptureSettings, DecodeSettings, Session
from .processing import analyze


def analyze_session(session):
    cfg = session.capture
    return analyze(read_analog(session.directory / f"analog_{cfg.positive_channel}.bin"),
                   read_analog(session.directory / f"analog_{cfg.negative_channel}.bin"), session.decode)


def write_metadata(session):
    document = {"format_version": 1, "capture": asdict(session.capture),
                "decode": asdict(session.decode), "metadata": session.metadata}
    (session.directory / "session.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def load_session(directory):
    directory = Path(directory)
    document = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    if document.get("format_version") != 1:
        raise ValueError("Unsupported session format.")
    session = Session(directory, CaptureSettings(**document["capture"]),
                      DecodeSettings(**document["decode"]), document.get("metadata", {}))
    session.capture.validate()
    session.decode.validate()
    return session


def save_session(session, destination):
    """Commit a complete new session directory without overwriting existing data."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Choose a new folder name; existing session folders are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".session-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "session"
        staging.mkdir()
        for channel in (session.capture.positive_channel, session.capture.negative_channel):
            shutil.copy2(session.directory / f"analog_{channel}.bin", staging)
        saved = Session(staging, session.capture, session.decode, session.metadata)
        write_metadata(saved)
        staging.rename(destination)
    return destination


def export_csv(frames, path):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["start_s", "end_s", "value", "hex", "character", "errors"])
        for f in frames:
            writer.writerow([f"{f.start:.12g}", f"{f.end:.12g}", f.value if f.value is not None else "",
                             f"{f.value:02X}" if f.value is not None else "", f.character, ";".join(f.errors)])
