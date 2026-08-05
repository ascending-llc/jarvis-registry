from registry_pkgs.models import ExtendedSkillFile
from registry_pkgs.models._generated import SkillFile


class TestSkillFile:
    def test_is_binary_is_declared_on_generated_model(self):
        assert SkillFile.__annotations__["isBinary"] == bool | None
        assert SkillFile.model_fields["isBinary"].default is None

    def test_extended_model_adds_no_fields(self):
        assert ExtendedSkillFile.__dict__.get("__annotations__", {}) == {}
        assert ExtendedSkillFile.model_fields["isBinary"].default is None

    def test_is_binary_preserves_all_three_states(self):
        assert SkillFile.model_construct(isBinary=True).isBinary is True
        assert SkillFile.model_construct(isBinary=False).isBinary is False
        assert SkillFile.model_construct().isBinary is None
