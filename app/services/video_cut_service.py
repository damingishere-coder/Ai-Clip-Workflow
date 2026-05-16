from pathlib import Path


def build_ffmpeg_cut_command(
    source_video: Path,
    output_video: Path,
    start_time: str,
    end_time: str,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        start_time,
        "-to",
        end_time,
        "-i",
        str(source_video),
        "-c",
        "copy",
        str(output_video),
    ]
