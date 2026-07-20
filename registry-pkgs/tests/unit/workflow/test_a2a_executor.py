from unittest.mock import patch

from a2a.types import Artifact, Part, Task, TaskState, TaskStatus, TextPart

from registry_pkgs.workflows.a2a_client import A2ACallResult
from registry_pkgs.workflows.a2a_executor import (
    _a2a_result_to_step_output,
    _extract_data_uri_media,
)

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
WAV_B64 = "UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA=="
CSV_B64 = "aWQsbmFtZSxzY29yZQoxLGFscGhhLDAuOTUKMixiZXRhLDAuODcKMyxnYW1tYSwwLjcyCg=="
MP4_B64 = (
    "AAAAGGZ0eXBpc29tAAAAAGlzb21tcDQxAAAAfG1vb3YAAAB0bXZoZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
)


def _empty_buckets():
    return {"files": [], "images": [], "videos": [], "audio": []}


def _datauri_block(name: str, mime: str, b64: str) -> str:
    return f"### {name} ({mime})\ndata:{mime};base64,{b64}\n"


def test_extracts_all_media_types_into_buckets():
    text = (
        "Media echo (datauri mode): hello\n\n"
        + _datauri_block("red_pixel.png", "image/png", PNG_B64)
        + _datauri_block("minimal.mp4", "video/mp4", MP4_B64)
        + _datauri_block("silence.wav", "audio/wav", WAV_B64)
        + _datauri_block("sample.csv", "text/csv", CSV_B64)
    )
    buckets = _empty_buckets()

    cleaned = _extract_data_uri_media(text, **buckets)

    assert [i.id for i in buckets["images"]] == ["red_pixel.png"]
    assert [v.id for v in buckets["videos"]] == ["minimal.mp4"]
    assert [a.id for a in buckets["audio"]] == ["silence.wav"]
    assert [f.name for f in buckets["files"]] == ["sample.csv"]
    assert "base64," not in cleaned
    assert "[media: red_pixel.png (image/png)]" in cleaned
    assert "[media: sample.csv (text/csv)]" in cleaned
    assert cleaned.startswith("Media echo (datauri mode): hello")


def test_filename_falls_back_to_generated_when_no_header():
    text = f"inline image data:image/png;base64,{PNG_B64} end"
    buckets = _empty_buckets()

    cleaned = _extract_data_uri_media(text, **buckets)

    assert len(buckets["images"]) == 1
    assert buckets["images"][0].id == "inline-1.png"
    assert "[media: inline-1.png (image/png)]" in cleaned


def test_header_with_mismatched_mime_is_ignored_for_naming():
    text = f"### notes.txt (text/plain)\ndata:image/png;base64,{PNG_B64}\n"
    buckets = _empty_buckets()

    _extract_data_uri_media(text, **buckets)

    assert buckets["images"][0].id == "inline-1.png"


def test_text_without_data_uris_is_returned_unchanged():
    text = "plain text output, no media at all"
    buckets = _empty_buckets()

    assert _extract_data_uri_media(text, **buckets) == text
    assert all(not bucket for bucket in buckets.values())


def test_invalid_base64_is_left_in_text():
    text = "broken data:image/png;base64,@@@not-base64@@@ tail"
    buckets = _empty_buckets()

    cleaned = _extract_data_uri_media(text, **buckets)

    # The regex only matches base64 alphabet, so the malformed payload never matches;
    # a syntactically-valid-but-undecodable payload is also kept.
    padded_garbage = "A" * 5  # length%4 != 0 → b64decode raises
    text2 = f"data:image/png;base64,{padded_garbage}"
    cleaned2 = _extract_data_uri_media(text2, **buckets)

    assert cleaned == text
    assert cleaned2 == text2
    assert not buckets["images"]


def test_oversized_payload_is_left_in_text():
    with patch("registry_pkgs.workflows.a2a_executor._DATA_URI_MAX_DECODED_BYTES", 10):
        text = f"data:image/png;base64,{PNG_B64}"
        buckets = _empty_buckets()

        cleaned = _extract_data_uri_media(text, **buckets)

    assert cleaned == text
    assert not buckets["images"]


def test_count_cap_extracts_only_first_n():
    with patch("registry_pkgs.workflows.a2a_executor._DATA_URI_MAX_COUNT", 1):
        text = f"data:image/png;base64,{PNG_B64}\ndata:audio/wav;base64,{WAV_B64}"
        buckets = _empty_buckets()

        cleaned = _extract_data_uri_media(text, **buckets)

    assert len(buckets["images"]) == 1
    assert not buckets["audio"]
    assert f"data:audio/wav;base64,{WAV_B64}" in cleaned
    assert "base64," not in cleaned.split("\n")[0]


def _task_with_text(text: str) -> Task:
    return Task(
        id="task-1",
        context_id="ctx-1",
        kind="task",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="art-1",
                name="Result",
                parts=[Part(root=TextPart(kind="text", text=text))],
            )
        ],
    )


def test_step_output_promotes_data_uris_from_task_text():
    text = "answer\n\n" + _datauri_block("red_pixel.png", "image/png", PNG_B64)
    result = A2ACallResult(task=_task_with_text(text), success=True)

    output = _a2a_result_to_step_output(result)

    assert output.success is True
    assert output.images is not None and output.images[0].id == "red_pixel.png"
    assert "base64," not in (output.content or "")
    assert "[media: red_pixel.png (image/png)]" in (output.content or "")


def test_step_output_without_data_uris_has_no_media():
    result = A2ACallResult(task=_task_with_text("plain answer"), success=True)

    output = _a2a_result_to_step_output(result)

    assert output.content is not None and "plain answer" in output.content
    assert output.images is None
    assert output.files is None
