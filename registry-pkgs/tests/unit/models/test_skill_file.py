from registry_pkgs.models import ExtendedSkillFile
from registry_pkgs.models._generated import SkillFile


class TestSkillFile:
    def test_is_binary_is_declared_on_generated_model(self):
        assert SkillFile.__annotations__["isBinary"] is bool
        assert SkillFile.model_fields["isBinary"].default is False

    def test_extended_model_adds_no_fields(self):
        assert ExtendedSkillFile.__dict__.get("__annotations__", {}) == {}
        assert ExtendedSkillFile.model_fields["isBinary"].default is False

    def test_is_binary_preserves_bool_states(self):
        assert SkillFile.model_construct(isBinary=True).isBinary is True
        assert SkillFile.model_construct(isBinary=False).isBinary is False
        assert SkillFile.model_construct().isBinary is False

    def test_is_executable_is_declared_on_generated_model(self):
        assert SkillFile.__annotations__["isExecutable"] is bool
        assert SkillFile.model_fields["isExecutable"].default is False

    def test_is_executable_preserves_bool_states(self):
        assert SkillFile.model_construct(isExecutable=True).isExecutable is True
        assert SkillFile.model_construct(isExecutable=False).isExecutable is False
        assert SkillFile.model_construct().isExecutable is False
