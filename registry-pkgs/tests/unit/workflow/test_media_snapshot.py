"""Tests for media_snapshot — metadata-only StepOutput media persistence and replay shells."""

from agno.media import Audio, File, Image, Video
from agno.workflow import StepOutput

from registry_pkgs.workflows.media_snapshot import (
    media_from_snapshot,
    serialize_step_output_media,
)


class TestSerializeStepOutputMedia:
    def test_text_only_output_yields_empty_dict(self):
        assert serialize_step_output_media(StepOutput(content="just text")) == {}

    def test_bytes_are_never_persisted(self):
        output = StepOutput(
            content="x",
            images=[Image(content=b"raw-image-bytes", id="pic.jpg", mime_type="image/jpeg")],
        )
        snapshot = serialize_step_output_media(output)
        assert snapshot["images"] == [{"id": "pic.jpg", "mime_type": "image/jpeg"}]
        assert b"raw-image-bytes" not in repr(snapshot).encode()

    def test_all_media_kinds_serialized(self):
        output = StepOutput(
            images=[Image(url="https://cdn/x.png", id="x.png", mime_type="image/png")],
            videos=[Video(content=b"v", id="v.mp4", mime_type="video/mp4")],
            audio=[Audio(content=b"a", id="a.wav", mime_type="audio/wav", transcript="hello")],
            files=[
                File(
                    content='{"k": 1}',
                    mime_type="application/json",
                    filename="meta.json",
                    name="meta.json",
                )
            ],
        )
        snapshot = serialize_step_output_media(output)
        assert snapshot["images"] == [{"id": "x.png", "url": "https://cdn/x.png", "mime_type": "image/png"}]
        assert snapshot["videos"] == [{"id": "v.mp4", "mime_type": "video/mp4"}]
        assert snapshot["audio"] == [{"id": "a.wav", "mime_type": "audio/wav", "transcript": "hello"}]
        assert snapshot["files"] == [{"mime_type": "application/json", "filename": "meta.json", "name": "meta.json"}]

    def test_file_size_kept_as_number(self):
        file = File(id="f", filename="f.bin", size=1234)
        snapshot = serialize_step_output_media(StepOutput(files=[file]))
        assert snapshot["files"][0]["size"] == 1234


class TestMediaFromSnapshot:
    def test_url_image_rebuilds_with_url(self):
        media = media_from_snapshot({"images": [{"id": "x.png", "url": "https://cdn/x.png", "mime_type": "image/png"}]})
        (image,) = media["images"]
        assert isinstance(image, Image)
        assert image.url == "https://cdn/x.png"
        assert image.mime_type == "image/png"

    def test_bytes_image_rebuilds_as_empty_shell(self):
        media = media_from_snapshot({"images": [{"id": "pic.jpg", "mime_type": "image/jpeg"}]})
        (image,) = media["images"]
        assert image.content == b""
        assert image.id == "pic.jpg"

    def test_missing_keys_yield_none(self):
        media = media_from_snapshot({"content": "text only"})
        assert media == {"images": None, "videos": None, "audio": None, "files": None}

    def test_malformed_entries_are_skipped(self):
        media = media_from_snapshot({"files": ["not-a-dict", {"filename": "ok.bin", "id": "ok.bin"}]})
        (file,) = media["files"]
        assert file.filename == "ok.bin"

    def test_file_without_identity_gets_filename_as_id(self):
        media = media_from_snapshot({"files": [{"filename": "orphan.bin"}]})
        (file,) = media["files"]
        assert file.id == "orphan.bin"

    def test_round_trip_preserves_prompt_summary(self):
        """The key P2 guarantee: replayed shells render the same prompt summary as live media."""
        from registry_pkgs.workflows.helpers import step_output_to_prompt_text

        live = StepOutput(
            content="see media",
            images=[Image(content=b"big-bytes", id="pic.jpg", mime_type="image/jpeg")],
            files=[File(id="doc.pdf", filename="doc.pdf", file_type="application/pdf")],
        )
        snapshot = serialize_step_output_media(live)
        media = media_from_snapshot(snapshot)
        replayed = StepOutput(content="see media", **media)
        assert step_output_to_prompt_text(replayed) == step_output_to_prompt_text(live)
