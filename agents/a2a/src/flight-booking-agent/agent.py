"""Flight Booking Agent - Main application module."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from dependencies import (
    get_db_manager,
    get_env,
)
from fastapi import FastAPI
from strands import Agent
from strands.multiagent.a2a import A2AServer
from tools import (
    FLIGHT_BOOKING_TOOLS,
    check_availability,
    confirm_booking,
    manage_reservation,
    process_payment,
    reserve_flight,
)

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

env_settings = get_env()

strands_agent = Agent(
    name="Flight Booking Agent",
    description="Flight booking and reservation management agent",
    tools=FLIGHT_BOOKING_TOOLS,
    callback_handler=None,
    model=env_settings.bedrock_model_id,
)

a2a_server = A2AServer(agent=strands_agent, http_url=env_settings.agent_url, serve_at_root=True)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    """Application lifespan manager."""
    # Setups before server startup
    get_db_manager()
    logger.info("Flight Booking Agent starting up")
    logger.info(f"Agent URL: {env_settings.agent_url}")
    logger.info(f"Listening on {env_settings.host}:{env_settings.port}")

    # TODO: register agent with MCP Gateway Registry when path available

    yield
    # Triggered after server shutdown
    logger.info("Flight Booking Agent shutting down")


app = FastAPI(title="Flight Booking Agent", lifespan=lifespan)


@app.get("/ping")
def ping():
    """Health check endpoint."""
    logger.debug("Ping endpoint called")
    return {"status": "healthy"}


@app.get("/api/health")
def health():
    """Health status endpoint."""
    logger.debug("Health endpoint called")
    return {"status": "healthy", "agent": "flight_booking"}


@app.post("/api/check-availability")
def api_check_availability(
    flight_id: int,
):
    """Check flight availability API endpoint."""
    logger.info(f"Checking availability for flight_id: {flight_id}")
    result = check_availability(flight_id)
    logger.debug(f"Availability check result: {result}")
    return {"result": result}


@app.post("/api/reserve-flight")
def api_reserve_flight(
    flight_id: int,
    passengers: list,
    requested_seats: list | None = None,
):
    """Reserve flight API endpoint."""
    logger.info(f"Reserving flight_id: {flight_id} for {len(passengers)} passengers")
    logger.debug(f"Passengers: {passengers}")
    logger.debug(f"Requested seats: {requested_seats}")
    result = reserve_flight(flight_id, passengers, requested_seats)
    logger.debug(f"Reservation result: {result}")
    return {"result": result}


@app.post("/api/confirm-booking")
def api_confirm_booking(
    booking_number: str,
):
    """Confirm booking API endpoint."""
    logger.info(f"Confirming booking: {booking_number}")
    result = confirm_booking(booking_number)
    logger.debug(f"Booking confirmation result: {result}")
    return {"result": result}


@app.post("/api/process-payment")
def api_process_payment(
    booking_number: str,
    payment_method: str,
    amount: float | None = None,
):
    """Process payment API endpoint."""
    logger.info(f"Processing payment for booking: {booking_number}")
    logger.debug(f"Payment method: {payment_method}, Amount: {amount}")
    result = process_payment(booking_number, payment_method, amount)
    logger.debug(f"Payment processing result: {result}")
    return {"result": result}


@app.get("/api/reservation/{booking_number}")
def api_get_reservation(
    booking_number: str,
):
    """Get reservation details API endpoint."""
    logger.info(f"Retrieving reservation: {booking_number}")
    result = manage_reservation(booking_number, "view")
    logger.debug(f"Reservation details: {result}")
    return {"result": result}


@app.delete("/api/reservation/{booking_number}")
def api_cancel_reservation(
    booking_number: str,
    reason: str = "User requested cancellation",
):
    """Cancel reservation API endpoint."""
    logger.info(f"Canceling reservation: {booking_number}")
    logger.debug(f"Cancellation reason: {reason}")
    result = manage_reservation(booking_number, "cancel", reason)
    logger.debug(f"Cancellation result: {result}")
    return {"result": result}


app.mount("/", a2a_server.to_fastapi_app())


def main() -> None:
    """Main entry point for the application."""
    logger.info("Starting Flight Booking Agent server")
    uvicorn.run(app, host=env_settings.host, port=env_settings.port)


if __name__ == "__main__":
    main()
