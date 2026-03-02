import path from "node:path";

import {
  DEFAULT_IMPORT_LIST,
  IMPORT_ANTHROPIC_SCRIPT,
  REGISTRY_CLI_WRAPPER,
  TEST_ANTHROPIC_SCRIPT
} from "../paths.js";
import type {ScriptCommand, ScriptTask, TaskCategory, TaskContext} from "./types.js";

const trim = (value: string | undefined): string => value?.trim() ?? "";

const buildBashCommand = (scriptPath: string, args: string[], env?: Record<string, string>): ScriptCommand => ({
  command: "bash",
  args: [scriptPath, ...args],
  env
});

const buildUvPythonCommand = (scriptPath: string, args: string[], env?: Record<string, string>): ScriptCommand => ({
  command: "uv",
  args: ["run", "python", scriptPath, ...args],
  env
});

// Build command for Registry Management API wrapper
const buildRegistryCommand = (args: string[], context: TaskContext): ScriptCommand => {
  const baseArgs = [
    "run",
    "python",
    REGISTRY_CLI_WRAPPER,
    "--base-url",
    context.gatewayBaseUrl
  ];

  // Add token from context if available (backend token takes precedence)
  if (context.backendToken) {
    // Token is already a string, pass it as environment variable
    // since the wrapper can read from GATEWAY_TOKEN env var
    return {
      command: "uv",
      args: [...baseArgs, ...args],
      env: {
        ...process.env,
        GATEWAY_TOKEN: context.backendToken
      }
    };
  }

  return {
    command: "uv",
    args: [...baseArgs, ...args],
    env: process.env as Record<string, string>
  };
};

const computeGatewayEnv = (context: TaskContext): Record<string, string> => ({
  ...process.env,
  GATEWAY_URL: context.gatewayBaseUrl
});

const serviceTasks: ScriptTask[] = [
  {
    key: "service-add",
    label: "Add service from config",
    description: "Validate the config and register the service via MCP gateway tools.",
    fields: [
      {
        name: "configPath",
        label: "Config file path",
        placeholder: "cli/examples/server-config.json"
      }
    ],
    build(values, context) {
      const configPath = trim(values.configPath);
      if (!configPath) {
        throw new Error("Config file path is required.");
      }
      return buildRegistryCommand(
        ["service", "add", configPath],
        context
      );
    }
  },
  {
    key: "service-delete",
    label: "Delete service",
    description: "Remove a service by path and name and clean up group assignments.",
    fields: [
      {
        name: "servicePath",
        label: "Service path (e.g. /example-server)",
        placeholder: "/example-server"
      },
      {
        name: "serviceName",
        label: "Service name",
        placeholder: "example-server"
      }
    ],
    build(values, context) {
      const servicePath = trim(values.servicePath);
      const serviceName = trim(values.serviceName);
      if (!servicePath || !serviceName) {
        throw new Error("Service path and name are required.");
      }
      return buildRegistryCommand(
        ["service", "delete", servicePath],
        context
      );
    }
  },
  {
    key: "service-monitor",
    label: "Monitor services",
    description: "Run health checks for all services or a specific config.",
    fields: [
      {
        name: "configPath",
        label: "Optional config file path",
        placeholder: "(leave blank for all services)",
        optional: true
      }
    ],
    build(values, context) {
      // Monitor is essentially list with detailed output
      return buildRegistryCommand(
        ["service", "list"],
        context
      );
    }
  },
  {
    key: "service-create-group",
    label: "Create group",
    description: "Create a Keycloak group for MCP servers.",
    fields: [
      {
        name: "groupName",
        label: "Group name",
        placeholder: "mcp-servers-team-x"
      },
      {
        name: "description",
        label: "Description",
        placeholder: "Team X access",
        optional: true
      }
    ],
    build(values, context) {
      const groupName = trim(values.groupName);
      if (!groupName) {
        throw new Error("Group name is required.");
      }
      const description = trim(values.description);
      const args = description
        ? ["group", "create", "--name", groupName, "--description", description]
        : ["group", "create", "--name", groupName];
      return buildRegistryCommand(args, context);
    }
  },
  {
    key: "service-delete-group",
    label: "Delete group",
    description: "Delete a Keycloak group.",
    fields: [
      {
        name: "groupName",
        label: "Group name",
        placeholder: "mcp-servers-team-x"
      }
    ],
    build(values, context) {
      const groupName = trim(values.groupName);
      if (!groupName) {
        throw new Error("Group name is required.");
      }
      return buildRegistryCommand(
        ["group", "delete", "--name", groupName],
        context
      );
    }
  },
  {
    key: "service-list-groups",
    label: "List groups",
    description: "List Keycloak groups.",
    fields: [],
    build(_values, context) {
      return buildRegistryCommand(
        ["group", "list"],
        context
      );
    }
  }
];

const importTasks: ScriptTask[] = [
  {
    key: "import-anthropic-dry",
    label: "Anthropic import (dry run)",
    description: "Preview the servers that would be imported from the Anthropic registry.",
    fields: [
      {
        name: "importList",
        label: "Import list file",
        placeholder: DEFAULT_IMPORT_LIST,
        optional: true,
        defaultValue: DEFAULT_IMPORT_LIST
      }
    ],
    build(values, context) {
      const importList = trim(values.importList);
      const args = ["--dry-run"];
      if (importList) {
        args.push("--import-list", importList);
      }
      return buildBashCommand(
        IMPORT_ANTHROPIC_SCRIPT,
        args,
        computeGatewayEnv(context)
      );
    }
  },
  {
    key: "import-anthropic-apply",
    label: "Anthropic import (apply)",
    description: "Fetch and register servers from the Anthropic MCP registry.",
    fields: [
      {
        name: "importList",
        label: "Import list file",
        placeholder: DEFAULT_IMPORT_LIST,
        optional: true,
        defaultValue: DEFAULT_IMPORT_LIST
      }
    ],
    build(values, context) {
      const importList = trim(values.importList);
      const args: string[] = [];
      if (importList) {
        args.push("--import-list", importList);
      }
      return buildBashCommand(
        IMPORT_ANTHROPIC_SCRIPT,
        args,
        computeGatewayEnv(context)
      );
    }
  }
];

const userTasks: ScriptTask[] = [
  {
    key: "user-create-m2m",
    label: "Create M2M service account",
    description: "Creates a service account client with group assignments (requires Keycloak admin access).",
    fields: [
      {
        name: "name",
        label: "Service account name",
        placeholder: "agent-finance-bot"
      },
      {
        name: "groups",
        label: "Groups (comma separated)",
        placeholder: "mcp-servers-finance/read,mcp-servers-finance/execute"
      },
      {
        name: "description",
        label: "Description",
        placeholder: "Finance bot account",
        optional: true
      }
    ],
    build(values, context) {
      const name = trim(values.name);
      const groups = trim(values.groups);
      const description = trim(values.description);
      if (!name || !groups) {
        throw new Error("Name and groups are required.");
      }
      const args = [
        "user",
        "create-m2m",
        "--name",
        name,
        "--groups",
        groups
      ];
      if (description) {
        args.push("--description", description);
      }
      return buildRegistryCommand(args, context);
    }
  },
  {
    key: "user-create-human",
    label: "Create human user",
    description: "Create a human user in Keycloak with group assignments.",
    fields: [
      {name: "username", label: "Username", placeholder: "jdoe"},
      {name: "email", label: "Email", placeholder: "jdoe@example.com"},
      {name: "firstName", label: "First name", placeholder: "John"},
      {name: "lastName", label: "Last name", placeholder: "Doe"},
      {
        name: "groups",
        label: "Groups (comma separated)",
        placeholder: "mcp-servers-restricted/read"
      },
      {
        name: "password",
        label: "Initial password (optional)",
        placeholder: "(leave blank to be prompted later)",
        optional: true
      }
    ],
    build(values, context) {
      const username = trim(values.username);
      const email = trim(values.email);
      const firstName = trim(values.firstName);
      const lastName = trim(values.lastName);
      const groups = trim(values.groups);
      const password = trim(values.password);
      if (!username || !email || !firstName || !lastName || !groups) {
        throw new Error("Username, email, first name, last name, and groups are required.");
      }
      const args = [
        "user",
        "create-human",
        "--username",
        username,
        "--email",
        email,
        "--first-name",
        firstName,
        "--last-name",
        lastName,
        "--groups",
        groups
      ];
      if (password) {
        args.push("--password", password);
      }
      return buildRegistryCommand(args, context);
    }
  },
  {
    key: "user-delete",
    label: "Delete user",
    description: "Delete a user (service account or human) from Keycloak.",
    fields: [
      {
        name: "username",
        label: "Username",
        placeholder: "agent-finance-bot"
      }
    ],
    build(values, context) {
      const username = trim(values.username);
      if (!username) {
        throw new Error("Username is required.");
      }
      return buildRegistryCommand(["user", "delete", "--username", username], context);
    }
  },
  {
    key: "user-list-users",
    label: "List users",
    description: "List all users in the Keycloak realm.",
    fields: [],
    build(_values, context) {
      return buildRegistryCommand(["user", "list"], context);
    }
  },
  {
    key: "user-list-groups",
    label: "List groups",
    description: "List all groups in Keycloak.",
    fields: [],
    build(_values, context) {
      return buildRegistryCommand(["group", "list"], context);
    }
  }
];

const diagnosticTasks: ScriptTask[] = [
  {
    key: "diagnostic-run-suite",
    label: "Run Anthropic API suite",
    description: "Run the full Anthropic MCP Registry API smoke test.",
    fields: [
      {
        name: "tokenFile",
        label: "Token file path",
        placeholder: ".oauth-tokens/ingress.json"
      },
      {
        name: "baseUrl",
        label: "Base URL",
        placeholder: "http://localhost",
        optional: true,
        defaultValue: "http://localhost"
      }
    ],
    build(values, context) {
      const tokenFile = trim(values.tokenFile);
      const baseUrl = trim(values.baseUrl);
      if (!tokenFile) {
        throw new Error("Token file path is required.");
      }
      const args = ["anthropic", "list", "--limit", "100"];
      if (baseUrl) {
        args.push("--base-url", baseUrl);
      }
      return buildRegistryCommand(args, context);
    }
  },
  {
    key: "diagnostic-run-test",
    label: "Run specific Anthropic API test",
    description: "Call a specific API test case (e.g., list-servers, get-server).",
    fields: [
      {
        name: "tokenFile",
        label: "Token file path",
        placeholder: ".oauth-tokens/ingress.json"
      },
      {
        name: "testName",
        label: "Test name",
        placeholder: "list-servers"
      },
      {
        name: "serverName",
        label: "Server name (for get-server)",
        placeholder: "io.mcpgateway/currenttime",
        optional: true
      },
      {
        name: "baseUrl",
        label: "Base URL",
        placeholder: "http://localhost",
        optional: true,
        defaultValue: "http://localhost"
      }
    ],
    build(values, context) {
      const tokenFile = trim(values.tokenFile);
      const testName = trim(values.testName);
      const serverName = trim(values.serverName);
      const baseUrl = trim(values.baseUrl);
      if (!tokenFile || !testName) {
        throw new Error("Token file and test name are required.");
      }

      // Map test name to Anthropic API command
      if (testName === "get-server" && serverName) {
        const args = ["anthropic", "get", serverName];
        if (baseUrl) {
          args.push("--base-url", baseUrl);
        }
        return buildRegistryCommand(args, context);
      } else {
        const args = ["anthropic", "list", "--limit", "100"];
        if (baseUrl) {
          args.push("--base-url", baseUrl);
        }
        return buildRegistryCommand(args, context);
      }
    }
  }
];

export const taskCatalog: Record<TaskCategory, ScriptTask[]> = {
  service: serviceTasks,
  import: importTasks,
  user: userTasks,
  diagnostic: diagnosticTasks
};

export const getTaskByKey = (category: TaskCategory, key: string): ScriptTask | undefined =>
  taskCatalog[category].find((task) => task.key === key);

export const resolveDefaultValues = (task: ScriptTask): Record<string, string> =>
  task.fields.reduce<Record<string, string>>((acc, field) => {
    if (typeof field.defaultValue === "string") {
      acc[field.name] = field.defaultValue;
    }
    return acc;
  }, {});
