"""
JSON Schema to Beanie ODM Model Generator

This script converts JSON schemas to Python Beanie Document models for PyMongo + Beanie ODM.
Supports two modes: Local (read from local files) and Remote (download from GitHub Release).

Usage:
    # Local mode - convert from local JSON files
    uv run import-schemas --mode local --input-dir DIR --files FILE1 FILE2 ... --output-dir DIR

    # Remote mode - download from GitHub Release
    uv run import-schemas --mode remote --tag VERSION --files FILE1 FILE2 ... --output-dir DIR

Examples:
    # Local mode - convert from local directory (recommended for development)
    uv run import-schemas --mode local --input-dir ./dist/json-schemas --files user.json token.json --output-dir ./models

    # Local mode - batch conversion
    uv run import-schemas --mode local --input-dir ./dist/json-schemas --files user.json token.json mcpServer.json session.json --output-dir ./app/models

    # Remote mode - download from GitHub Release (private repository)
    uv run import-schemas --mode remote --tag asc0.4.0 --files user.json token.json --output-dir ./models --token GitHub_Token --repo ascending-llc/jarvis-api

    # Remote mode - public repository (--mode remote is default, can be omitted)
    uv run import-schemas --tag asc0.4.0 --files user.json token.json --output-dir ./models

Features:
    - No third-party dependencies (uses only Python stdlib)
    - Local mode: Fast, no network required, ideal for development
    - Remote mode: Download from GitHub Release, ideal for CI/CD
    - Generates Beanie Document classes with proper type hints
    - Handles timestamps, references (Link), and nested documents
    - Generates StrEnum classes from JSON Schema $defs (x-enum-members)
    - Cross-platform compatible (Windows/Linux/macOS)
    - Intentionally skipping index definition - indices are always maintained from the jarvis-api side.
"""

import argparse
import json
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inflection import underscore


class NestedModels:
    def __init__(self) -> None:
        self._models: list[list[str]] = []
        self._identifiers: set[str] = set()

    def append(self, model: list[str]) -> None:
        if len(model) == 0:
            return

        if model[0] in self._identifiers:
            return

        self._identifiers.add(model[0])
        self._models.append(model)

    def to_list(self) -> list[list[str]]:
        return self._models


class BeanieModelGenerator:
    """Generate Beanie ODM models from JSON Schema"""

    # Type mapping from JSON Schema to Python types
    TYPE_MAPPING = {
        "string": "str",
        "number": "float",
        "integer": "int",
        "boolean": "bool",
        "array": "List",
        "object": "Dict",
    }

    def __init__(self, output_dir: str, github_repo: str = "ascending-llc/jarvis-api", github_token: str | None = None):
        self.output_dir = Path(output_dir)
        self.generated_dir = self.output_dir / "_generated"
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.github_repo = github_repo
        self.github_token = github_token
        self.imported_types: set[str] = set()
        self.nested_models = NestedModels()  # Store nested model definitions

    def cleanup_generated_files(self):
        """
        Clean up existing generated files and caches to prevent import issues.
        Removes all .py files in _generated/ and all __pycache__ directories.
        """

        if not self.generated_dir.exists():
            return

        print("Cleaning up existing generated files and caches...")

        # Remove all .py files in _generated directory (except __init__.py will be recreated)
        for py_file in self.generated_dir.glob("*.py"):
            try:
                py_file.unlink()
                print(f"  Removed: {py_file.name}")
            except Exception as e:
                print(f"  Warning: Could not remove {py_file.name}: {e}")

        # Remove __pycache__ in _generated directory
        pycache_dir = self.generated_dir / "__pycache__"
        if pycache_dir.exists():
            try:
                shutil.rmtree(pycache_dir)
                print("  Removed: _generated/__pycache__/")
            except Exception as e:
                print(f"  Warning: Could not remove __pycache__: {e}")

        # Remove parent __pycache__ directories
        parent_pycache = self.output_dir / "__pycache__"
        if parent_pycache.exists():
            try:
                shutil.rmtree(parent_pycache)
                print("  Removed: models/__pycache__/")
            except Exception as e:
                print(f"  Warning: Could not remove parent __pycache__: {e}")

        print("Cleanup completed.\n")

    def load_local_schema(self, input_dir: str, filename: str) -> dict[str, Any]:
        """
        Load JSON schema from local directory

        Args:
            input_dir: Local directory containing JSON schema files
            filename: JSON schema filename

        Returns:
            Parsed JSON schema dictionary
        """
        if not filename.endswith(".json"):
            filename += ".json"

        schema_path = Path(input_dir) / filename

        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)

        return schema

    def download_schema(self, tag: str, filename: str) -> dict[str, Any]:
        """
        Download JSON schema from GitHub Release using API.

        For private repositories, direct download links return 404.
        We need to use GitHub API to get asset ID and then download.
        """
        if not filename.endswith(".json"):
            filename += ".json"

        try:
            owner, repo = self.github_repo.split("/")
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"

            headers = {"User-Agent": "Jarvis-Schema-Downloader", "Accept": "application/vnd.github.v3+json"}

            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"

            # Create SSL context that doesn't verify certificates (for Windows compatibility)
            ssl_context = ssl._create_unverified_context()  # nosec B323 - needed for Windows compatibility

            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ssl_context) as response:  # nosec B310 - the schema is HTTPS and doesn't allow variation
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                release_data = json.loads(response.read().decode("utf-8"))

            asset_id = None
            for asset in release_data.get("assets", []):
                if asset["name"] == filename:
                    asset_id = asset["id"]
                    break

            if not asset_id:
                available_files = [a["name"] for a in release_data.get("assets", [])]
                raise Exception(
                    f"File '{filename}' not found in release {tag}. "
                    f"Available files: {', '.join(available_files) if available_files else 'none'}"
                )

            download_url = f"https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}"
            headers["Accept"] = "application/octet-stream"

            req = urllib.request.Request(download_url, headers=headers)
            with urllib.request.urlopen(req, context=ssl_context) as response:  # nosec B310 - the schema is HTTPS and doesn't allow variation
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                content = response.read().decode("utf-8")
                schema = json.loads(content)
                return schema

        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise Exception(f"Release '{tag}' or file '{filename}' not found") from e
            elif e.code == 401:
                raise Exception("Invalid or missing GitHub token") from e
            raise
        except Exception:
            raise

    def list_release_json_files(self, tag: str) -> list[str]:
        """
        List all .json files available in a GitHub Release.

        Args:
            tag: GitHub Release tag/version

        Returns:
            List of .json filenames available in the release
        """
        try:
            owner, repo = self.github_repo.split("/")
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"

            headers = {"User-Agent": "Jarvis-Schema-Downloader", "Accept": "application/vnd.github.v3+json"}

            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"

            # Create SSL context that doesn't verify certificates (for Windows compatibility)
            ssl_context = ssl._create_unverified_context()  # nosec B323 - needed for Windows compatibility

            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ssl_context) as response:  # nosec B310 - the schema is HTTPS and doesn't allow variation
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                release_data = json.loads(response.read().decode("utf-8"))

            # Extract all .json filenames
            json_files = [asset["name"] for asset in release_data.get("assets", []) if asset["name"].endswith(".json")]

            return json_files

        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise Exception(f"Release '{tag}' not found") from e
            elif e.code == 401:
                raise Exception("Invalid or missing GitHub token") from e
            raise
        except Exception:
            raise

    def list_local_json_files(self, input_dir: str) -> list[str]:
        """
        List all .json files in a local directory.

        Args:
            input_dir: Local directory path

        Returns:
            List of .json filenames in the directory
        """
        dir_path = Path(input_dir)

        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {input_dir}")

        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {input_dir}")

        # Get all .json files
        json_files = [f.name for f in dir_path.glob("*.json")]

        return sorted(json_files)

    def generate_model(self, schema: dict[str, Any]) -> tuple[str, str, list[str]]:
        """Generate Python Beanie model code from JSON schema

        Returns:
            tuple: (model_code, class_name, enum_class_names)
        """
        self.imported_types = set()
        self.nested_models = NestedModels()

        title = schema.get("title", "Document")
        collection_name = schema.get("x-collection", title.lower())
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        has_timestamps = schema.get("x-timestamps", False)

        # Parse $defs for named enum definitions
        self._schema_enum_lookup: dict[tuple, str] = {}
        self._schema_enum_defs: dict[str, dict[str, Any]] = {}
        defs = schema.get("$defs", {})
        for enum_name, enum_def in defs.items():
            members = enum_def.get("x-enum-members")
            if members:
                self._schema_enum_defs[enum_name] = members
                enum_key = tuple(enum_def.get("enum", []))
                if enum_key:
                    self._schema_enum_lookup[enum_key] = enum_name

        lines = []

        enum_class_names = []
        if self._schema_enum_defs:
            self.imported_types.add("StrEnum")
            for enum_name in sorted(self._schema_enum_defs):
                members = self._schema_enum_defs[enum_name]
                lines.append(f"class {enum_name}(StrEnum):")
                for member_name, member_value in members.items():
                    lines.append(f'    {member_name} = "{member_value}"')
                lines.append("")
                lines.append("")
                enum_class_names.append(enum_name)

        # Generate nested models first
        for field_name, field_def in properties.items():
            self._extract_nested_models(field_name, field_def, title)

        # Add nested model definitions in reverse order (so dependencies come first)
        # This ensures that nested models referenced by other nested models are defined first
        nested_models_list = self.nested_models.to_list()
        if len(nested_models_list) > 0:
            for nested_model in reversed(nested_models_list):
                lines.extend(nested_model)
                lines.append("")
                lines.append("")

        # Generate main model
        lines.append(f"class {title}(Document):")
        lines.append('    """')
        lines.append(f"    {title} Model")
        lines.append("")

        if "x-generated" in schema:
            gen_info = schema["x-generated"]
            lines.append(f"    Generated from: {gen_info.get('source_file', 'N/A')}")
            lines.append(f"    Schema version: {gen_info.get('version', 'N/A')}")
            lines.append(f"    Generated at: {gen_info.get('timestamp', 'N/A')}")

        lines.append('    """')
        lines.append("")

        field_lines = []
        for field_name, field_def in properties.items():
            field_line = self._generate_field(field_name, field_def, field_name in required_fields)
            if field_line:
                field_lines.append(field_line)

        if not field_lines:
            field_lines.append("    pass")

        lines.extend(field_lines)

        lines.append("")
        lines.append("    class Settings:")
        lines.append(f'        name = "{collection_name}"')

        # Configure Beanie to not save None values to MongoDB
        # This prevents sparse index conflicts on optional fields
        lines.append("        keep_nulls = False")

        if has_timestamps:
            lines.append("        use_state_management = True")

        imports = self._generate_imports(has_timestamps)

        return imports + "\n\n" + "\n".join(lines), title, enum_class_names

    def _extract_nested_models(self, field_name: str, field_def: dict[str, Any], parent_name: str):
        """
        Extract nested model definitions from field definitions

        Args:
            field_name: Name of the field
            field_def: Field definition from JSON schema
            parent_name: Name of the parent model
        """
        json_type = field_def.get("type")

        # Handle array with nested object
        if json_type == "array":
            items = field_def.get("items", {})
            if items.get("type") == "object":
                properties = items.get("properties")
                if properties:
                    # Generate nested model class
                    schema_ref = items.get("x-schema-ref")
                    if schema_ref:
                        # Use the schema reference name
                        nested_class_name = schema_ref.replace("Schema", "")
                    else:
                        # Generate class name from field name
                        nested_class_name = self._field_name_to_class_name(field_name)

                    nested_model = self._generate_nested_model(nested_class_name, properties, items.get("required", []))
                    self.nested_models.append(nested_model)

        # Handle nested object
        elif json_type == "object":
            properties = field_def.get("properties")
            if properties:
                # Check if this nested object has x-schema-ref (like AuthSchema)
                schema_ref = field_def.get("x-schema-ref")
                if schema_ref:
                    # Use the schema reference name
                    nested_class_name = schema_ref.replace("Schema", "")
                else:
                    # Generate class name from field name
                    nested_class_name = self._field_name_to_class_name(field_name)

                nested_model = self._generate_nested_model(nested_class_name, properties, field_def.get("required", []))
                self.nested_models.append(nested_model)

                # Recursively extract nested models from sub-properties
                for sub_field_name, sub_field_def in properties.items():
                    self._extract_nested_models(sub_field_name, sub_field_def, nested_class_name)

    def _field_name_to_class_name(self, field_name: str) -> str:
        """
        Convert field name to class name

        Examples:
            backupCodes -> BackupCode
            refreshToken -> RefreshToken
            personalization -> Personalization
        """
        # Remove trailing 's' for plural forms
        if field_name.endswith("s") and len(field_name) > 1:
            field_name = field_name[:-1]

        # Convert camelCase to PascalCase
        if field_name and field_name[0].islower():
            field_name = field_name[0].upper() + field_name[1:]

        return field_name

    def _generate_nested_model(
        self, class_name: str, properties: dict[str, Any], required_fields: list[str]
    ) -> list[str]:
        """
        Generate a nested Pydantic BaseModel class

        Args:
            class_name: Name of the nested class
            properties: Field properties
            required_fields: List of required field names (from 'required' array)

        Returns:
            List of code lines for the nested model
        """
        lines = []
        lines.append(f"class {class_name}(BaseModel):")
        lines.append(f'    """Nested model for {class_name}"""')

        # Collect fields marked with x-required
        x_required_fields = [name for name, defn in properties.items() if defn.get("x-required")]
        all_required = list(set(required_fields + x_required_fields))

        field_lines = []
        for field_name, field_def in properties.items():
            field_line = self._generate_field(field_name, field_def, field_name in all_required)
            if field_line:
                field_lines.append(field_line)

        if not field_lines:
            field_lines.append("    pass")

        lines.extend(field_lines)

        # Mark that we need to import BaseModel
        self.imported_types.add("BaseModel")

        return lines

    def _normalize_model_reference(self, ref_name: str) -> str:
        """
        Normalize model reference name to match actual class names.

        Handles various naming conventions in x-ref:
        - "user" / "User" → "IUser"
        - "AccessRole" → "IAccessRole"
        - "Token" → "Token" (unchanged)
        - etc.
        """
        # Common mappings for models with "I" prefix
        ref_mappings = {
            "user": "IUser",
            "User": "IUser",
            "AccessRole": "IAccessRole",
            "AclEntry": "IAclEntry",
            "Group": "IGroup",
        }

        # Check if there's a direct mapping
        if ref_name in ref_mappings:
            return ref_mappings[ref_name]

        # If the reference already starts with "I" and is capitalized, keep it
        if ref_name.startswith("I") and len(ref_name) > 1 and ref_name[1].isupper():
            return ref_name

        # Otherwise, return as-is (for models like Token, Session, etc.)
        return ref_name

    def _collect_field_indexes(self, properties: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Collect field-level index definitions from x-index, x-unique, and x-sparse.
        This method is defined but not used, because jarvis-api should be the source of truth on indices.
        Whenever its backend connects to MongoDB via Mongoose,
        it makes sure indices are all created according to schema definitions.
        The Python side is only concerned with the field shape of the collections,
        it doesn't need to **define** indices via Beanie again.
        """
        field_indexes = []

        for field_name, field_def in properties.items():
            # Check if field has index-related properties
            has_unique = field_def.get("x-unique", False)
            has_index = field_def.get("x-index", False)
            has_sparse = field_def.get("x-sparse", False)

            if has_unique or has_index:
                index_def = {"fields": {field_name: 1}}

                # Add options if present
                if has_unique:
                    index_def["unique"] = True
                if has_sparse:
                    index_def["sparse"] = True

                field_indexes.append(index_def)

        return field_indexes

    def _generate_field(self, field_name: str, field_def: dict[str, Any], is_required: bool) -> str:
        """Generate a single field definition"""
        field_attrs = []
        comments = []

        # Handle fields with leading underscores - Pydantic doesn't allow them
        # Use alias to preserve the database field name
        python_field_name = field_name
        if field_name.startswith("_"):
            python_field_name = field_name.lstrip("_")
            field_attrs.append(f'alias="{field_name}"')
            comments.append(f"DB field: {field_name}")

        # Handle reference fields FIRST - use PydanticObjectId for direct ObjectId storage
        ref_model = field_def.get("x-ref")
        if ref_model:
            # Normalize the reference name to match actual class names
            normalized_ref = self._normalize_model_reference(ref_model)
            # Use PydanticObjectId for references (stores plain ObjectId, not DBRef)
            field_type = "PydanticObjectId"
            self.imported_types.add("PydanticObjectId")
            comments.append(f"references {normalized_ref} collection")
        else:
            # Get python type if not a reference
            field_type = self._get_python_type(field_def, field_name)

        # Handle auto-generated fields (like timestamps)
        if field_def.get("x-auto-generated"):
            field_type = f"Optional[{field_type}]"
            field_attrs.append("default=None")
            comments.append("auto-generated by Beanie")

        # Skip default handling if already handled by x-auto-generated
        if not field_def.get("x-auto-generated"):
            if "default" in field_def:
                default_val = self._format_default(field_def["default"])
                if not is_required:
                    field_type = f"Optional[{field_type}]"
                field_attrs.append(f"default={default_val}")
            elif "x-default-expression" in field_def:
                expr = field_def["x-default-expression"]
                if "Date.now" in expr:
                    # Handle Date.now() - can be converted to Python
                    field_attrs.append("default_factory=lambda: datetime.now(timezone.utc)")
                    self.imported_types.add("datetime")
                    self.imported_types.add("timezone")
                else:
                    # For other expressions (like SystemRoles.USER, enums, constants)
                    # Try to generate a reasonable placeholder or keep as required field

                    # Strategy 1: If it looks like an enum/constant (has dots), suggest a placeholder
                    if "." in expr:
                        # Extract the last part as hint: SystemRoles.USER -> USER
                        parts = expr.split(".")
                        hint = parts[-1] if len(parts) > 1 else "unknown"

                        # Generate a placeholder string default
                        field_attrs.append(f'default="{hint}"')
                        comments.append(f"TODO: Verify default value. Original: {expr}")
                    else:
                        # Strategy 2: For simple expressions, mark as required but add clear note
                        if not is_required:
                            field_type = f"Optional[{field_type}]"
                            field_attrs.append("default=None")
                        else:
                            field_attrs.append("...")
                        comments.append(f"DB default: {expr}")
            else:
                if not is_required:
                    field_type = f"Optional[{field_type}]"
                    field_attrs.append("default=None")
                else:
                    field_attrs.append("...")

        if field_def.get("type") == "string":
            if "minLength" in field_def:
                field_attrs.append(f"min_length={field_def['minLength']}")
            if "maxLength" in field_def:
                field_attrs.append(f"max_length={field_def['maxLength']}")
            if "pattern" in field_def and field_type != "PydanticObjectId" and not field_type.startswith("Link"):
                pattern = field_def["pattern"]
                field_attrs.append(f'pattern=r"{pattern}"')

        if field_attrs:
            field_call = f"Field({', '.join(field_attrs)})"
        else:
            field_call = "Field(...)"

        result = f"    {python_field_name}: {field_type} = {field_call}"
        if comments:
            result += f"  # {', '.join(comments)}"

        return result

    def _get_python_type(self, field_def: dict[str, Any], field_name: str = "") -> str:
        """Convert JSON Schema type to Python type"""
        json_type = field_def.get("type")

        # Handle enum values — prefer named StrEnum from $defs, fall back to Literal
        if "enum" in field_def:
            enum_values = field_def["enum"]
            if enum_values:
                enum_key = tuple(enum_values)
                if enum_key in self._schema_enum_lookup:
                    return self._schema_enum_lookup[enum_key]
                # Fallback to Literal type
                self.imported_types.add("Literal")
                formatted_values = ", ".join(f'"{v}"' for v in enum_values)
                return f"Literal[{formatted_values}]"

        if field_def.get("pattern") == "^[0-9a-fA-F]{24}$":
            self.imported_types.add("PydanticObjectId")
            return "PydanticObjectId"

        if json_type == "string" and field_def.get("format") == "date-time":
            self.imported_types.add("datetime")
            return "datetime"

        if json_type == "array":
            self.imported_types.add("List")
            items = field_def.get("items", {})
            if items:
                # Check if items has properties (nested model)
                if items.get("type") == "object" and items.get("properties"):
                    # Use the nested model class name
                    schema_ref = items.get("x-schema-ref")
                    if schema_ref:
                        nested_class_name = schema_ref.replace("Schema", "")
                    else:
                        nested_class_name = self._field_name_to_class_name(field_name)
                    return f"List[{nested_class_name}]"
                else:
                    item_type = self._get_python_type(items, field_name)
                    return f"List[{item_type}]"
            else:
                return "List[Any]"

        # Handle nested objects with properties (structured sub-documents)
        if json_type == "object":
            properties = field_def.get("properties")
            if properties:
                # Check if this nested object has x-schema-ref (like AuthSchema)
                schema_ref = field_def.get("x-schema-ref")
                if schema_ref:
                    # Use the schema reference name
                    nested_class_name = schema_ref.replace("Schema", "")
                else:
                    # Use the nested model class name
                    nested_class_name = self._field_name_to_class_name(field_name)
                return nested_class_name
            elif field_def.get("x-mixed"):
                self.imported_types.add("Dict")
                return "Dict[str, Any]"
            else:
                self.imported_types.add("Dict")
                return "Dict[str, Any]"

        if "additionalProperties" in field_def:
            self.imported_types.add("Dict")
            return "Dict[str, Any]"

        python_type = self.TYPE_MAPPING.get(json_type, "Any")

        if python_type in ("List", "Dict"):
            self.imported_types.add(python_type)

        return python_type

    def _format_default(self, value: Any) -> str:
        """Format default value for Python code"""
        if value is None:
            return "None"
        elif isinstance(value, bool):
            return "True" if value else "False"
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, list):
            return "[]"
        elif isinstance(value, dict):
            return "{}"
        else:
            return repr(value)

    def _generate_index(self, index_def: dict[str, Any]) -> str:
        """
        Generate index definition for Settings.indexes. This method is defined but not used, because jarvis-api
        should be the source of truth on indices. Whenever its backend connects to MongoDB via Mongoose,
        it makes sure indices are all created according to schema definitions. The Python side is only concerned
        with the field shape of the collections, it doesn't need to **define** indices via Beanie again.

        Rules (Updated to fix Beanie/PyMongo compatibility):
        - No options → list: [("field1", 1), ("field2", -1)]
        - With options → IndexModel: IndexModel([("field", 1)], unique=True)

        Note: Beanie's _validate method passes the value directly to IndexModel(),
        so we cannot use tuple format like ([...], {...}) as it gets interpreted
        as keys instead of being unpacked.
        """
        fields = index_def.get("fields", {})

        field_list = []
        for field_name, direction in fields.items():
            field_list.append(f'("{field_name}", {direction})')

        if not field_list:
            return ""

        # Check if there are any options
        options = []
        if index_def.get("unique"):
            options.append("unique=True")
        if index_def.get("sparse"):
            options.append("sparse=True")
        if "expireAfterSeconds" in index_def:
            options.append(f"expireAfterSeconds={index_def['expireAfterSeconds']}")

        # Handle partialFilterExpression
        if "x-partialFilterExpression" in index_def:
            filter_expr = index_def["x-partialFilterExpression"]
            python_filter = self._convert_filter_expression(filter_expr)
            if python_filter:
                options.append(f"partialFilterExpression={python_filter}")

        has_options = len(options) > 0

        # Apply formatting rules
        if has_options:
            # With options → IndexModel object
            self.imported_types.add("IndexModel")
            return f"IndexModel([{', '.join(field_list)}], {', '.join(options)})"
        else:
            # No options → list
            return f"[{', '.join(field_list)}]"

    def _convert_filter_expression(self, filter_expr: str) -> str | None:
        """Convert MongoDB filter expression from JS format to Python dict format"""
        try:
            # Simple conversion for common patterns
            # idOnTheSource: { $exists: true } -> {"idOnTheSource": {"$exists": True}}

            # Extract field name and operator
            match = re.search(r"(\w+):\s*\{\s*\$(\w+):\s*(\w+)\s*\}", filter_expr)
            if match:
                field_name = match.group(1)
                operator = match.group(2)
                value = match.group(3)

                # Convert JS boolean to Python boolean
                if value == "true":
                    py_value = "True"
                elif value == "false":
                    py_value = "False"
                else:
                    py_value = value

                return f'{{"{field_name}": {{"${operator}": {py_value}}}}}'

            return None
        except Exception:
            return None

    def _generate_imports(self, has_timestamps: bool) -> str:
        """Generate import statements based on used types"""
        imports = []

        if "StrEnum" in self.imported_types:
            imports.append("from enum import StrEnum")

        datetime_imports = []
        if "datetime" in self.imported_types:
            datetime_imports.append("datetime")
        if "timezone" in self.imported_types:
            datetime_imports.append("timezone")

        if datetime_imports:
            imports.append(f"from datetime import {', '.join(datetime_imports)}")

        typing_imports = []
        if "List" in self.imported_types:
            typing_imports.append("List")
        if "Dict" in self.imported_types:
            typing_imports.append("Dict")
        if "Literal" in self.imported_types:
            typing_imports.append("Literal")
        typing_imports.append("Optional")
        typing_imports.append("Any")

        if typing_imports:
            imports.append(f"from typing import {', '.join(sorted(typing_imports))}")

        # Pydantic imports
        pydantic_imports = ["Field"]
        if "BaseModel" in self.imported_types:
            pydantic_imports.append("BaseModel")
        imports.append(f"from pydantic import {', '.join(pydantic_imports)}")

        beanie_imports = ["Document"]
        if "PydanticObjectId" in self.imported_types:
            beanie_imports.append("PydanticObjectId")
        if "Link" in self.imported_types:
            beanie_imports.append("Link")

        imports.append(f"from beanie import {', '.join(beanie_imports)}")

        return "\n".join(imports)

    def save_model(self, model_code: str, filename: str) -> Path:
        """Save generated model to file in _generated directory"""
        module_name = Path(filename).stem
        module_name = underscore(module_name)
        py_file = self.generated_dir / f"{module_name}.py"

        with open(py_file, "w", encoding="utf-8") as f:
            f.write(model_code)

        return py_file

    def _generate_init_with_parent(self, model_info: list[tuple]):
        """
        Generate __init__.py to export all models (includes parent __init__.py)

        Args:
            model_info: List of tuples (module_name, class_name) for each model
        """
        generated_init = self.generated_dir / "__init__.py"

        lines = ['"""']
        lines.append("Auto-generated Beanie ODM Models")
        lines.append("")
        lines.append("⚠️  DO NOT EDIT - This directory is auto-generated")
        lines.append(f"Generated at: {datetime.now(UTC).isoformat()}")
        lines.append('"""')
        lines.append("")

        for module_name, class_name in model_info:
            lines.append(f"from .{module_name} import {class_name}")

        lines.append("")
        lines.append("__all__ = [")
        for _module_name, class_name in model_info:
            lines.append(f'    "{class_name}",')
        lines.append("]")
        lines.append("")

        with open(generated_init, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Generate parent __init__.py that exports from _generated
        parent_init = self.output_dir / "__init__.py"
        parent_lines = ['"""']
        parent_lines.append("Beanie ODM Models")
        parent_lines.append("")
        parent_lines.append("Exports auto-generated models from _generated/")
        parent_lines.append('"""')
        parent_lines.append("")
        parent_lines.append("from ._generated import (")

        for _module_name, class_name in model_info:
            parent_lines.append(f"    {class_name},")

        parent_lines.append(")")
        parent_lines.append("")
        parent_lines.append("__all__ = [")
        for _module_name, class_name in model_info:
            parent_lines.append(f'    "{class_name}",')
        parent_lines.append("]")
        parent_lines.append("")

        with open(parent_init, "w", encoding="utf-8") as f:
            f.write("\n".join(parent_lines))

        return generated_init

    def generate_init_file(self, models_info: list[tuple[str, str, list[str]]]):
        """Generate __init__.py in _generated directory only

        Args:
            models_info: List of (filename, class_name, enum_names) tuples
        """
        # Generate _generated/__init__.py
        generated_init = self.generated_dir / "__init__.py"
        lines = ['"""']
        lines.append("Auto-generated Beanie ODM Models")
        lines.append("")
        lines.append("DO NOT EDIT - This directory is auto-generated")
        lines.append(f"Generated at: {datetime.now(UTC).isoformat()}")
        lines.append('"""')
        lines.append("")

        # Collect all exportable symbols: (module_name, symbol_name)
        all_symbols: list[tuple[str, str]] = []
        for filename, class_name, enum_names in models_info:
            module = Path(filename).stem
            all_symbols.append((module, class_name))
            for enum_name in enum_names:
                all_symbols.append((module, enum_name))

        # Sort by symbol name for consistent output
        all_symbols.sort(key=lambda x: x[1])

        for module, symbol in all_symbols:
            lines.append(f"from .{module} import {symbol}")

        lines.append("")
        lines.append("__all__ = [")
        for _, symbol in all_symbols:
            lines.append(f'    "{symbol}",')
        lines.append("]")
        lines.append("")

        with open(generated_init, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return generated_init

    def generate_readme(self, version: str, files: list[str], repo: str):
        """Generate README.md in _generated directory with generation instructions"""
        readme_file = self.generated_dir / "README.md"

        lines = ["# Auto-Generated Models"]
        lines.append("")
        lines.append("⚠️  **DO NOT EDIT FILES IN THIS DIRECTORY**")
        lines.append("")
        lines.append("This directory contains auto-generated Beanie ODM models from JSON schemas.")
        lines.append("")
        lines.append("## Generation Info")
        lines.append("")
        lines.append(f"- **Repository**: {repo}")
        lines.append(f"- **Version**: {version}")
        lines.append(f"- **Generated at**: {datetime.now(UTC).isoformat()}")
        lines.append(f"- **Files**: {len(files)}")
        lines.append("")
        lines.append("## Regenerate Models")
        lines.append("")
        lines.append("To regenerate these models, run the following command from project root.")
        lines.append("")
        lines.append("```bash")
        lines.append("uv run poe generate-schemas")
        lines.append("```")
        lines.append("")
        lines.append("## Files Generated")
        lines.append("")
        for f in files:
            module = Path(f).stem
            lines.append(f"- `{module}.py`")
        lines.append("")

        with open(readme_file, "w", encoding="utf-8") as fr:
            fr.write("\n".join(lines))

        return readme_file

    def save_schema_version(self, version: str):
        """Save schema version to .schema-version file"""
        version_file = self.generated_dir / ".schema-version"

        with open(version_file, "w", encoding="utf-8") as fw:
            fw.write(f"{version}\n")

        return version_file


def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description="Convert JSON schemas to Beanie ODM models (supports local and remote modes)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local mode - convert specific files
  uv run import-schemas --mode local --input-dir ./dist/json-schemas --files user.json token.json --output-dir ./models

  # Local mode - convert all .json files in directory
  uv run import-schemas --mode local --input-dir ./dist/json-schemas --output-dir ./models

  # Remote mode - download specific files from GitHub Release
  uv run import-schemas --mode remote --tag asc0.4.0 --files user.json token.json --output-dir ./models --token ghp_xxxxx

  # Remote mode - download all .json files from GitHub Release
  uv run import-schemas --mode remote --tag asc0.4.0 --output-dir ./models --token ghp_xxxxx

  # Remote mode (default) - download all files from public repository
  uv run import-schemas --tag asc0.4.0 --output-dir ./models

GitHub Release URL format:
  https://github.com/{repo}/releases/download/{tag}/{filename}
        """,
    )

    # Mode selection
    parser.add_argument(
        "--mode",
        choices=["local", "remote"],
        default="remote",
        help="Mode: local (read from local directory) or remote (download from GitHub). Default: remote",
    )

    # Common arguments
    parser.add_argument(
        "--files",
        nargs="+",
        required=False,
        help="JSON schema filenames to convert (e.g., user.json token.json). If not specified, all .json files will be processed.",
    )
    parser.add_argument("--output-dir", required=True, help="Output directory for generated Python models")

    # Local mode arguments
    parser.add_argument(
        "--input-dir", help="[Local mode] Input directory containing JSON schema files (required for --mode local)"
    )

    # Remote mode arguments
    parser.add_argument(
        "--tag", help="[Remote mode] GitHub Release tag/version (e.g., asc0.4.0) (required for --mode remote)"
    )
    parser.add_argument(
        "--repo",
        default="ascending-llc/jarvis-api",
        help="[Remote mode] GitHub repository (default: ascending-llc/jarvis-api)",
    )
    parser.add_argument(
        "--token",
        help="[Remote mode] GitHub Personal Access Token (for private repos). Can also use GITHUB_TOKEN env var",
    )

    args = parser.parse_args()

    # Validate mode-specific required arguments
    if args.mode == "local":
        if not args.input_dir:
            parser.error("--input-dir is required when --mode is 'local'")
    elif args.mode == "remote" and not args.tag:
        parser.error("--tag is required when --mode is 'remote'")

    # Print mode information
    print("=" * 70)
    print("JSON Schema to Beanie ODM Model Converter")
    print("=" * 70)
    print(f"Mode: {args.mode.upper()}")

    generator = BeanieModelGenerator(output_dir=args.output_dir, github_repo=args.repo, github_token=args.token)

    # Clean up existing files and caches to prevent import issues
    generator.cleanup_generated_files()

    generated_files = []
    model_info = []  # List of (module_name, class_name, enum_names) tuples
    failed_files = []

    if args.mode == "local":
        print(f"Input directory: {args.input_dir}")
        print(f"Output directory: {args.output_dir}")

        # Discover all .json files if --files not provided
        if not args.files:
            print("Discovering all .json files in directory...")
            try:
                args.files = generator.list_local_json_files(args.input_dir)
                print(f"Found {len(args.files)} .json file(s): {', '.join(args.files)}")
            except Exception as e:
                print(f"Error discovering files: {e}")
                return 1

        print(f"Files to convert: {len(args.files)}\n")

        for filename in args.files:
            try:
                print(f"Processing: {filename}")
                schema = generator.load_local_schema(args.input_dir, filename)
                model_code, class_name, enum_names = generator.generate_model(schema)
                py_file = generator.save_model(model_code, filename)
                generated_files.append(py_file)

                module_name = py_file.stem
                model_info.append((module_name, class_name, enum_names))

                enum_str = f" + enums: {', '.join(enum_names)}" if enum_names else ""
                print(f"  Generated: {py_file.name} (class: {class_name}{enum_str})")
            except Exception as e:
                print(f"  Error: {e}")
                failed_files.append((filename, str(e)))

    else:  # remote mode
        print(f"GitHub repository: {args.repo}")
        print(f"Release tag: {args.tag}")
        print(f"Output directory: {args.output_dir}")

        # Discover all .json files if --files not provided
        if not args.files:
            print("Discovering all .json files in release...")
            try:
                args.files = generator.list_release_json_files(args.tag)
                print(f"Found {len(args.files)} .json file(s): {', '.join(args.files)}")
            except Exception as e:
                print(f"Error discovering files: {e}")
                return 1

        print(f"Files to download: {len(args.files)}\n")

        for filename in args.files:
            try:
                print(f"Downloading: {filename}")
                schema = generator.download_schema(args.tag, filename)
                model_code, class_name, enum_names = generator.generate_model(schema)
                py_file = generator.save_model(model_code, filename)
                generated_files.append(py_file)

                module_name = py_file.stem
                model_info.append((module_name, class_name, enum_names))

                enum_str = f" + enums: {', '.join(enum_names)}" if enum_names else ""
                print(f"  Generated: {py_file.name} (class: {class_name}{enum_str})")
            except Exception as e:
                print(f"  Error: {e}")
                failed_files.append((filename, str(e)))

    print("\n" + "=" * 70)
    if generated_files:
        generator.generate_init_file(model_info)

        # Generate README and version file for remote mode
        if args.mode == "remote":
            generator.generate_readme(args.tag, args.files, args.repo)
            generator.save_schema_version(args.tag)
            print(f"Successfully generated {len(generated_files)} model(s) in _generated/")
            print(f"Schema version: {args.tag}")
        else:
            print(f"Successfully generated {len(generated_files)} model(s) in _generated/")

        if failed_files:
            print(f"Failed: {len(failed_files)} file(s)")
            for filename, error in failed_files:
                print(f"  - {filename}: {error}")
    else:
        print("No files were generated")
        if failed_files:
            print(f"Failed: {len(failed_files)} file(s)")
            for filename, error in failed_files:
                print(f"  - {filename}: {error}")
    print("=" * 70)

    return 1 if failed_files else 0


if __name__ == "__main__":
    sys.exit(main())
