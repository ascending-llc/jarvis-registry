# Azure AI Foundry Federation

<div style="text-align: center; margin: 0.5rem 0 1.25rem;">
	<img src="../../img/microsoft-azure-ai.svg" alt="Azure Foundry AI">
</div>

Azure AI Foundry Federation connects an external Azure AI Foundry workspace to Jarvis Registry so platform admins can import and govern agents from one control plane.

<div style="text-align: center; margin: 1.5rem 0; position: relative; display: inline-block;">
	<a href="https://ascendingdc.com/jarvis-ai/videos/azure-ai-foundry-federation-with-jarvis-registry-access-governed-agents-from-any-interface/" target="_blank" rel="noopener noreferrer" style="display: inline-block; position: relative;">
		<img src="https://img.youtube.com/vi/nXwltEynb98/maxresdefault.jpg" alt="Azure AI Foundry Federation — watch demo" style="max-width:100%; height:auto; display:block; border-radius:8px;" />
		<span style="position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); background:rgba(0,0,0,0.5); width:84px; height:84px; border-radius:50%; display:flex; align-items:center; justify-content:center;">
			<svg width="40" height="40" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
				<path d="M8 5v14l11-7L8 5z" fill="#fff" />
			</svg>
		</span>
	</a>
</div>

This page focuses on the operational flow:

1. Admin creates a federation connection in the UI.
2. Registry syncs A2A agents from the Azure AI Foundry workspace.
3. Admin reviews imported agents and shares them using the same ACL model used across the registry.

---

## Federation Creation Flow

A federation starts with a simple creation form in Jarvis Registry.

Admins provide:

- **Federation Name** — friendly name for the Azure AI Foundry source
- **Connection Settings** — Azure subscription ID, resource group, AI Foundry workspace name, and Entra ID tenant ID
- **Resource Tags Filter** — sync only specific tagged agents or sync all

After save, Jarvis Registry validates the connection using the provided Entra ID credentials and establishes the federation link.

---

## Automatic Import from Azure AI Foundry

Once federation is active, Jarvis Registry automatically pulls agents from the connected workspace.

Imported resources include:

- **A2A agents** available from the Azure AI Foundry Agent Service

Azure AI Foundry agents use non-standard AgentCard discovery paths (e.g. `agentCard/v0.3` rather than `/.well-known/agent-card.json`) and require Entra ID RBAC role assignments before invocation. The sync process normalizes these platform-specific differences — storing the custom discovery paths and auth prerequisites — so imported agents appear in the same catalog experience as local resources and can be invoked without platform-specific client code.

---

## Admin Governance After Sync

Federated agents are not automatically open to all users. Admins still control visibility and access.

After sync, admins can:

- Keep imported agents private for validation
- Share with specific users or groups
- Publish to everyone with VIEW access when ready

The sharing model is exactly the same as other resources in Jarvis Registry:

- Agent resources follow the same controls as [A2A Agent Registry share panel](a2a-registry.md#sharing-an-agent)

Security enforcement (authentication, RBAC, ACL) remains consistent for federated and local resources. See [Security Control Design](../design/security-design.md).

---

## Next Steps

- [Workflow Orchestration](workflow-orchestration.md) — How Foundry agents participate in multi-agent, MCP, and skill workflows
- [A2A Agent Registry](a2a-registry.md) — Sharing and lifecycle for agent resources
- [Registry Endpoint](registry-endpoint.md) — How clients discover and invoke federated resources
- [AgentCore Federation](agentcore-federation.md) — Federation from AWS AgentCore Runtime
- [Federation Guide](../design/federation.md) — Detailed federation architecture and workflow

**Product page & demo**

- Federation demo and product notes on ASCENDING: [AgentCore & AI Foundry federation](https://ascendingdc.com/jarvis-ai/agent-gateway/agentcore-aifoundry-federation/).
