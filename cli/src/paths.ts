import path from "node:path";
import {fileURLToPath} from "node:url";

const SRC_DIR = fileURLToPath(new URL(".", import.meta.url));
export const CLI_ROOT = path.resolve(SRC_DIR, "..");
export const REPO_ROOT = path.resolve(CLI_ROOT, "..");

// Modern Python API wrapper (replaces deprecated shell scripts)
export const REGISTRY_CLI_WRAPPER = path.join(CLI_ROOT, "registry_cli_wrapper.py");

// Legacy scripts (deprecated - kept for backwards compatibility)
export const IMPORT_ANTHROPIC_SCRIPT = path.join(CLI_ROOT, "import_from_anthropic_registry.sh");
export const TEST_ANTHROPIC_SCRIPT = path.join(CLI_ROOT, "test_anthropic_api.py");
export const DEFAULT_IMPORT_LIST = path.join(CLI_ROOT, "import_server_list.txt");
