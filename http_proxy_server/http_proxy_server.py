from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Union
import subprocess
import os
import httpx
import asyncio
import time as ttime
import psutil

# Configuration from environment variables
tempus_receiver_path = os.getenv(
    "TEMPUS_RECEIVER_PATH", "/opt/tempus-receiver/target/debug/tempus-receiver"
)
collection_server_url = os.getenv("COLLECTION_SERVER_URL", "http://10.66.82.30:8000")

# TODO - replace occurrences of destinations with these constants
# Constants
DEST1 = "100.0.0.10"
DEST2 = "100.0.0.11"

app = FastAPI(
    title="Tempus Receiver API",
    description="""
    ## FastAPI server for tempus-receiver CLI and detector control proxy

    This API provides HTTP endpoints to:
    1. Trigger data acquisition using the tempus-receiver CLI binary
    2. Proxy detector control commands to the collection server

    ### Data Acquisition Features:
    * **POST /acquire**: Start data acquisition with custom file path and period
    * **GET /status**: Get API status and configuration information
    * **GET /**: Health check endpoint

    ### System Monitoring Features:
    * **GET /network/status**: Get network interface statistics and data rates

    ### Detector Control Proxy Features:
    * **GET /detector/status**: Get detector status (temperature and current time)
    * **GET /detector/readtemp**: Read detector temperature
    * **POST /detector/reset**: Reset the detector
    * **POST /detector/arm**: Arm the detector for data acquisition
    * **POST /detector/init**: Initialize the detector with optional parameters
    * **POST /detector/acquire**: Acquire data for specified period

    ### Configuration:
    * The tempus-receiver binary path is configurable via the `TEMPUS_RECEIVER_PATH` environment variable
    * The collection server URL is configurable via the `COLLECTION_SERVER_URL` environment variable (default: http://10.66.82.30:8000)
    * Default destinations are hardcoded to `100.0.0.10` and `100.0.0.11`
    * File paths automatically get timestamps appended in ISO format

    ### Usage:
    - Send a POST request to `/acquire` with JSON payload containing `file_path` and `period` parameters for data acquisition
    - Use the `/detector/*` endpoints to control the detector through the proxy to the collection server
    """,
    version="1.0.0",
    contact={
        "name": "API Support",
        "email": "support@example.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


class DataAcquisitionRequest(BaseModel):
    """Request model for data acquisition"""

    file_path: str = Field(
        ...,
        description="Base file path for data storage. Timestamp will be appended automatically.",
        examples=["/nsls2/data/dssi/scratch/test"],
    )
    period: int = Field(
        ..., ge=1, description="Period for data acquisition in seconds", examples=[10]
    )


class DataAcquisitionResponse(BaseModel):
    """Response model for data acquisition"""

    success: bool
    message: str
    file_path: str
    command: str
    process_id: Optional[int] = None


class DetectorInitRequest(BaseModel):
    """Request model for detector initialization"""

    reset: bool = True
    num_inits: int = 1
    configure_dacs: bool = True
    load_pixel_config: bool = True


class DetectorAcquireRequest(BaseModel):
    """Request model for detector data acquisition"""

    period: float = Field(..., gt=0, description="Acquisition period in seconds")


class ProxyResponse(BaseModel):
    """Response model for proxy operations"""

    success: bool
    message: str
    data: Optional[Union[str, dict]] = None


@app.get("/", tags=["Health"])
async def root():
    """
    Health check endpoint

    Returns a simple message indicating the API is running.
    Use this endpoint to verify the service is operational.
    """
    return {"message": "Tempus Receiver API is running"}


@app.post("/acquire", response_model=DataAcquisitionResponse, tags=["Data Acquisition"])
async def acquire_data(request: DataAcquisitionRequest):
    """
    Trigger data acquisition using the tempus-receiver CLI

    This endpoint will:
    1. Generate a timestamped file path (appends current timestamp in ISO format)
    2. Execute the tempus-receiver CLI with the specified parameters
    3. Return the result and process information

    **Parameters:**
    - `file_path`: Base path for data storage (timestamp will be automatically appended)
    - `period`: Acquisition period in seconds (must be ≥ 1)

    **Returns:**
    - Success status and message
    - Generated timestamped file path
    - Full command executed
    - Process ID for tracking

    **Example:**
    ```json
    {
      "file_path": "/nsls2/data/dssi/scratch/test",
      "period": 10
    }
    ```
    """

    # Generate timestamped file path
    timestamped_path = f"{request.file_path}"

    # Use the configured tempus-receiver path

    # Check if the tempus-receiver binary exists
    if not os.path.exists(tempus_receiver_path):
        raise HTTPException(
            status_code=500,
            detail=f"tempus-receiver binary not found at {tempus_receiver_path}",
        )

    # Build the command with all required parameters
    command = [
        tempus_receiver_path,
        "-b",
        timestamped_path,
        "--dest1",
        DEST1,
        "--dest2",
        DEST2,
        "-r",
        "-t",
        str(request.period),
    ]
    print("comand:")
    print(" ".join(command))

    # Execute the command (non-blocking)
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    # Give the process a moment to start and check if it failed immediately
    import time

    time.sleep(0.1)  # Brief delay to catch immediate startup failures

    # Check if process is still running (not failed to start)
    poll_result = process.poll()
    if poll_result is not None:
        # Process has already terminated, get the output
        stdout, stderr = process.communicate()
        raise HTTPException(
            status_code=422,
            detail=f"tempus-receiver failed to start: returncode={poll_result}, stdout={stdout}, stderr={stderr}",
        )

    # Process is running, return success
    ret = DataAcquisitionResponse(
        success=True,
        message=f"Data acquisition started successfully with period {request.period}s",
        file_path=timestamped_path,
        command=" ".join(command),
        process_id=process.pid,
    )
    print(f"{ret = }")
    return ret


@app.get("/data/status", response_model=dict, tags=["System"])
async def get_data_status(process_id: int, file_path: str):
    """
    This endpoint checks the status of the data acquisition process.
    It answers the question: Is the data acquisition finished?

    **Returns:**
    Returns a dictionary object of the form:
    ```json
    {
        "process_finished": bool,
        "message": str,
        "file_sizes": dict
    }
    """
    # Check if process is still running (not failed to start)
    try:
        os.kill(process_id, 0)  # Does not kill the process, just checks if it exists
        process_finished = False
        message = "Data acquisition in progress"
    except OSError:  # process has finished
        process_finished = True
        message = "Process finished with no errors"

    # TODO - fix hardcoding of files
    file_paths = [file_path + suff for suff in ["_TOP.bin", "_BOT.bin"]]

    if os.path.exists(file_paths[0]) and os.path.exists(file_paths[1]):
        # Get size of files
        file_sizes = {fp: os.path.getsize(fp) for fp in file_paths}
        return {
            "process_finished": process_finished,
            "message": message,
            "file_sizes": file_sizes,
        }
    else:
        message = "Data acquisition started, but files do not exist"
        return {
            "process_finished": process_finished,
            "message": message,
            "file_sizes": {},
        }


@app.get("/status", tags=["System"])
async def get_status():
    """
    Get API status and configuration information

    Returns current system status, available endpoints, and configuration details.
    Useful for monitoring and debugging.

    **Returns:**
    - API status
    - Available endpoints with descriptions
    - Configured tempus-receiver binary path
    - Default destination addresses
    """
    return {
        "status": "running",
        "endpoints": {
            "POST /acquire": "Start data acquisition",
            "GET /status": "Get API status",
            "GET /": "Health check",
            "GET /network/status": "Get network interface statistics",
            "GET /detector/status": "Get detector status (proxy)",
            "GET /detector/readtemp": "Read detector temperature (proxy)",
            "POST /detector/reset": "Reset detector (proxy)",
            "POST /detector/arm": "Arm detector (proxy)",
            "POST /detector/init": "Initialize detector (proxy)",
            "POST /detector/acquire": "Acquire detector data (proxy)",
        },
        "tempus_receiver_path": tempus_receiver_path,
        "collection_server_url": collection_server_url,
        "default_destinations": {"dest1": "100.0.0.10", "dest2": "100.0.0.11"},
    }


async def read_net_stats(period=1.0, addresses=[DEST1, DEST2]):
    """
    Read network interface statistics over a specified period (non-blocking)

    Args:
        period: Time period in seconds to measure network activity (default: 1)
        interface: Network interface name to monitor (default: "ens1f1np1")

    Returns:
        Dictionary containing network statistics in bytes, kilobytes, and megabytes per second
    """
    start_time = ttime.monotonic()
    start = psutil.net_io_counters(pernic=True)
    psutil.net_io_counters.cache_clear()
    await asyncio.sleep(period)  # Non-blocking sleep
    stop = psutil.net_io_counters(pernic=True)
    stop_time = ttime.monotonic()
    duration = stop_time - start_time

    ip_to_interface = {
        info[0].address: interface for interface, info in psutil.net_if_addrs().items()
    }
    stats = {}
    for address in addresses:
        interface = ip_to_interface[address]
        in_bytes_per_sec = (
            stop[interface].bytes_recv - start[interface].bytes_recv
        ) / duration
        stats[interface] = {
            "address": address,
            "duration": duration,
            "in_bytes_per_sec": in_bytes_per_sec,
            "in_kilobytes_per_sec": in_bytes_per_sec / 2**10,
            "in_megabytes_per_sec": in_bytes_per_sec / 2**20,
        }
    return stats


@app.get("/network/status", tags=["System"])
async def get_network_status():
    """
    Get network interface statistics (non-blocking)

    This endpoint monitors network activity on a specified interface over a given time period
    and returns the incoming data rate in bytes, kilobytes, and megabytes per second.
    The measurement is performed asynchronously and will not block other API requests.

    **Parameters:**
    - `period`: Time period in seconds to measure network activity (default: 1, minimum: 1)
    - `interface`: Network interface name to monitor (default: "ens1f1np1")

    **Returns:**
    - Interface name
    - Measurement duration
    - Incoming data rate in bytes per second
    - Incoming data rate in kilobytes per second
    - Incoming data rate in megabytes per second

    **Note:** The request will take approximately the specified period duration to complete,
    but other API endpoints will remain responsive during this time.

    **Example:**
    ```
    GET /network/status?period=2&interface=eth0
    ```
    """

    status = {}

    try:
        stats = await read_net_stats(1.0, addresses=[DEST1, DEST2])
        for interface in stats.keys():
            status[interface] = stats[interface]["in_bytes_per_sec"]
        return status
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to read network statistics: {str(e)}"
        )


# HTTP client for communicating with collection server
async def call_collection_server(
    endpoint: str, method: str = "GET", data: Optional[dict] = None
) -> str:
    """Make HTTP request to collection server"""
    url = f"{collection_server_url}{endpoint}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            if method.upper() == "GET":
                response = await client.get(url)
            elif method.upper() == "POST":
                response = await client.post(url, json=data)
            else:
                raise HTTPException(
                    status_code=400, detail=f"Unsupported HTTP method: {method}"
                )

            response.raise_for_status()

            # Collection server returns plain text, not JSON
            return response.text

        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504, detail="Collection server request timed out"
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail=f"Cannot connect to collection server at {collection_server_url}",
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Collection server error: {e.response.text}",
            )

##############################################
#### Proxy endpoints for detector control ####
##############################################

@app.get("/detector/status", response_model=ProxyResponse, tags=["Detector Control"])
async def get_detector_status():
    """
    Get detector status (temperature and current time)

    This endpoint proxies the request to the collection server to get
    current detector status including temperature readings.
    """
    try:
        data = await call_collection_server("/")
        return ProxyResponse(
            success=True, message="Detector status retrieved successfully", data=data
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to get detector status: {e.detail}"
        )


@app.get("/detector/readtemp", response_model=ProxyResponse, tags=["Detector Control"])
async def read_detector_temperature():
    """
    Read detector temperature

    This endpoint proxies the request to the collection server to read
    the current detector temperature.
    """
    try:
        temp_text = await call_collection_server("/readtemp", "GET")
        # Collection server returns just the temperature value as text
        try:
            temperature = float(temp_text.strip())
        except ValueError:
            raise HTTPException(
                status_code=500, detail=f"Invalid temperature response: {temp_text}"
            )

        return ProxyResponse(
            success=True,
            message="Temperature read successfully",
            data={"temperature": temperature},
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to read temperature: {e.detail}"
        )


@app.post("/detector/reset", response_model=ProxyResponse, tags=["Detector Control"])
async def reset_detector():
    """
    Reset the detector

    This endpoint proxies the request to the collection server to reset
    the detector hardware.
    """
    try:
        data = await call_collection_server(
            "/", "POST", {"method": "reset", "kwargs": {}}
        )
        return ProxyResponse(
            success=True, message="Detector reset successfully", data=data
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to reset detector: {e.detail}"
        )


@app.post("/detector/arm", response_model=ProxyResponse, tags=["Detector Control"])
async def arm_detector():
    """
    Arm the detector

    This endpoint proxies the request to the collection server to arm
    the detector for data acquisition.
    """
    try:
        data = await call_collection_server(
            "/", "POST", {"method": "arm", "kwargs": {}}
        )
        return ProxyResponse(
            success=True, message="Detector armed successfully", data=data
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to arm detector: {e.detail}"
        )


@app.post("/detector/init", response_model=ProxyResponse, tags=["Detector Control"])
async def initialize_detector(request: DetectorInitRequest):
    """
    Initialize the detector with optional parameters

    This endpoint proxies the request to the collection server to initialize
    the detector with the specified parameters.

    **Parameters:**
    - `reset`: Whether to reset the detector before initialization (default: true)
    - `num_inits`: Number of initialization attempts (default: 1)
    - `configure_dacs`: Whether to configure DACs (default: true)
    - `load_pixel_config`: Whether to load pixel configuration (default: true)
    """
    try:
        data = await call_collection_server(
            "/", "POST", {"method": "init", "kwargs": request.model_dump()}
        )
        return ProxyResponse(
            success=True, message="Detector initialized successfully", data=data
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to initialize detector: {e.detail}"
        )


@app.post("/detector/acquire", response_model=ProxyResponse, tags=["Detector Control"])
async def acquire_detector_data(request: DetectorAcquireRequest):
    """
    Acquire data for specified period

    This endpoint proxies the request to the collection server to start
    data acquisition for the specified time period.

    **Parameters:**
    - `period`: Acquisition period in seconds (must be > 0)
    """
    try:
        data = await call_collection_server(
            "/", "POST", {"method": "acquire", "kwargs": request.model_dump()}
        )
        return ProxyResponse(
            success=True,
            message=f"Data acquisition started for {request.period} seconds",
            data=data,
        )
    except HTTPException as e:
        return ProxyResponse(
            success=False, message=f"Failed to start data acquisition: {e.detail}"
        )


@app.post("/detector/reset_link/{link_id}", response_model=dict)
async def reset_link(link_id: int):
    """
    Reset a specific detector link

    This endpoint proxies the request to the collection server to reset
    a specific detector link identified by `link_id`.
    """
    try:
        data = await call_collection_server(
            "/", "POST", {"method": "reset_link", "kwargs": {"link_id": link_id}}
        )
        return {
            "success": True,
            "message": f"Detector link {link_id} reset successfully",
            "data": data,
        }
    except HTTPException as e:
        return {
            "success": False,
            "message": f"Failed to reset detector link {link_id}: {e.detail}",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
