from .server import create_mcp_app, register_prompts, register_tools

__all__ = ["mcp_app"]

mcp_app = create_mcp_app()

# Register all components
register_prompts(mcp_app)
register_tools(mcp_app)
