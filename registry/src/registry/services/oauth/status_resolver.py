from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.schemas import OAuthFlow, OAuthFlowStatus

from ...schemas.enums import ConnectionState


class ConnectionStatusResolver:
    def __init__(self, flow_state_manager: FlowStateManager) -> None:
        self.flow_state_manager = flow_state_manager

    def resolve_oauth_state(self, flow: OAuthFlow | None, has_authorization: bool) -> str:
        expired = flow is not None and self.flow_state_manager.is_flow_expired(flow)
        if flow is not None and flow.status == OAuthFlowStatus.PENDING and not expired:
            return ConnectionState.CONNECTING.value
        if has_authorization:
            return ConnectionState.CONNECTED.value
        if flow is not None:
            cancelled = bool(flow.error and "cancelled" in flow.error.lower())
            if not cancelled and (
                flow.status == OAuthFlowStatus.FAILED or (expired and flow.status == OAuthFlowStatus.PENDING)
            ):
                return ConnectionState.ERROR.value
        return ConnectionState.DISCONNECTED.value
