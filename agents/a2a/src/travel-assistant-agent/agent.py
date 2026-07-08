"""Travel Assistant Agent - Main application module."""

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
    TRAVEL_ASSISTANT_TOOLS,
    check_prices,
    create_trip_plan,
    get_recommendations,
    search_flights,
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
    name="Travel Assistant Agent",
    description="Flight search and trip planning agent",
    tools=TRAVEL_ASSISTANT_TOOLS,
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
    logger.info("Travel Assistant Agent starting up")
    logger.info(f"Agent URL: {env_settings.agent_url}")
    logger.info(f"Listening on {env_settings.host}:{env_settings.port}")

    # TODO: register agent with MCP Gateway Registry when path available

    yield
    # Triggered after server shutdown
    logger.info("Travel Assistant Agent shutting down")


app = FastAPI(title="Travel Assistant Agent", lifespan=lifespan)


@app.get("/ping")
def ping():
    """Health check endpoint."""
    logger.debug("Ping endpoint called")
    return {"status": "healthy"}


@app.get("/api/health")
def health():
    """Health status endpoint."""
    logger.debug("Health endpoint called")
    return {"status": "healthy", "agent": "travel_assistant"}


@app.post("/api/search-flights")
def api_search_flights(
    departure_city: str,
    arrival_city: str,
    departure_date: str,
):
    """Search flights API endpoint."""
    logger.info(f"Searching flights: {departure_city} to {arrival_city} on {departure_date}")
    result = search_flights(departure_city, arrival_city, departure_date)
    logger.debug(f"Flight search result: {result}")
    return {"result": result}


@app.post("/api/check-prices")
def api_check_prices(
    flight_id: int,
):
    """Check prices API endpoint."""
    logger.info(f"Checking prices for flight_id: {flight_id}")
    result = check_prices(flight_id)
    logger.debug(f"Price check result: {result}")
    return {"result": result}


@app.get("/api/recommendations")
def api_recommendations(
    max_price: float,
    preferred_airlines: str | None = None,
):
    """Get recommendations API endpoint."""
    logger.info(f"Getting recommendations: max_price={max_price}, preferred_airlines={preferred_airlines}")
    airlines = preferred_airlines.split(",") if preferred_airlines else None
    result = get_recommendations(max_price, airlines)
    logger.debug(f"Recommendations result: {result}")
    return {"result": result}


@app.post("/api/create-trip-plan")
def api_create_trip_plan(
    departure_city: str,
    arrival_city: str,
    departure_date: str,
    return_date: str | None = None,
    budget: float | None = None,
):
    """Create trip plan API endpoint."""
    logger.info(f"Creating trip plan: {departure_city} to {arrival_city}, dates: {departure_date} - {return_date}")
    logger.debug(f"Budget: {budget}")
    result = create_trip_plan(departure_city, arrival_city, departure_date, return_date, budget)
    logger.debug(f"Trip plan result: {result}")
    return {"result": result}


app.mount("/", a2a_server.to_fastapi_app())


def main() -> None:
    """Main entry point for the application."""
    logger.info("Starting Travel Assistant Agent server")
    uvicorn.run(app, host=env_settings.host, port=env_settings.port)


if __name__ == "__main__":
    main()
