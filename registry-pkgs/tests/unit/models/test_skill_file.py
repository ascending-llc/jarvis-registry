from registry_pkgs.models import ExtendedSkillFile
from registry_pkgs.models._generated import SkillFile


class TestSkillFile:
    def test_is_binary_is_declared_on_generated_model(self):
        assert SkillFile.__annotations__["isBinary"] == bool | None
        assert SkillFile.model_fields["isBinary"].default is None

    def test_extended_model_adds_registry_storage_fields(self):
        assert SkillFile.__annotations__["source"] is str
        assert SkillFile.model_fields["source"].is_required()
        assert "source" not in ExtendedSkillFile.__annotations__
        assert ExtendedSkillFile.__annotations__["body"] == bytes | None
        assert ExtendedSkillFile.model_fields["body"].default is None
        assert ExtendedSkillFile.model_fields["isBinary"].default is None

    def test_is_binary_preserves_all_three_states(self):
        assert SkillFile.model_construct(isBinary=True).isBinary is True
        assert SkillFile.model_construct(isBinary=False).isBinary is False
        assert SkillFile.model_construct().isBinary is None

    def test_is_executable_is_declared_on_generated_model(self):
        assert SkillFile.__annotations__["isExecutable"] is bool
        assert SkillFile.model_fields["isExecutable"].default is False

    def test_is_executable_preserves_bool_states(self):
        assert SkillFile.model_construct(isExecutable=True).isExecutable is True
        assert SkillFile.model_construct(isExecutable=False).isExecutable is False
        assert SkillFile.model_construct().isExecutable is False
